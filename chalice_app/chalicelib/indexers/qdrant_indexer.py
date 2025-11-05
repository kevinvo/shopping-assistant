from langchain_openai import OpenAIEmbeddings
from typing import List, Dict, Any
from langchain.schema import Document
from dataclasses import dataclass
from chalicelib.config import config
from chalicelib.logger_config import setup_logger
from qdrant_client import QdrantClient
from qdrant_client.http import models
import uuid
from rank_bm25 import BM25Okapi
from chalicelib.performance_timer import measure_execution_time
from pydantic import SecretStr

logger = setup_logger(__name__)


@dataclass
class SearchResult:
    text: str
    metadata: Dict[str, Any]
    score: float = 0.0


class QdrantIndexer:
    def __init__(self):
        self.client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key,
            timeout=30.0,
        )
        self.embeddings = OpenAIEmbeddings(
            api_key=SecretStr(config.openai_api_key),
            model="text-embedding-3-small",  # This is the default model
        )
        self.collection_name = "reddit_posts"

    def delete_index(self) -> None:
        """Delete the Qdrant collection if it exists."""
        try:
            collections = self.client.get_collections().collections
            collection_names = [collection.name for collection in collections]

            if self.collection_name in collection_names:
                self.client.delete_collection(self.collection_name)
                logger.info(f"Deleted collection: {self.collection_name}")
            else:
                logger.info(f"Collection {self.collection_name} does not exist")

        except Exception as e:
            logger.error(f"Error deleting Qdrant collection: {e}")
            raise

    def create_index(self) -> None:
        """Create the Qdrant collection if it doesn't exist."""
        try:
            # Check if collection exists
            collections = self.client.get_collections().collections
            collection_names = [collection.name for collection in collections]

            if self.collection_name not in collection_names:
                # Create collection with hybrid search configuration
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={
                        "dense": models.VectorParams(
                            size=1536,  # OpenAI ada-002 embedding size
                            distance=models.Distance.COSINE,
                        ),
                        "sparse": models.VectorParams(
                            size=30,  # Size for BM25 sparse vectors (matches the actual dimension)
                            distance=models.Distance.DOT,
                        ),
                    },
                )
                logger.info(f"Created new collection: {self.collection_name}")
            else:
                logger.info(f"Collection {self.collection_name} already exists")

        except Exception as e:
            logger.error(f"Error creating Qdrant collection: {e}")
            raise

    def index_documents(self, docs: List[Document]) -> None:
        # Extract texts and metadata
        texts: List[str] = [doc.page_content for doc in docs]
        doc_ids = [
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    f"{doc.metadata['post_id']}_{doc.metadata['subreddit_name']}_"
                    f"{doc.metadata['chunk_id']}_{doc.metadata['type']}",
                )
            )
            for doc in docs
        ]

        # Generate dense embeddings
        dense_embeddings: List[List[float]] = self.embeddings.embed_documents(texts)

        # Generate sparse embeddings with BM25
        tokenized_texts = [text.lower().split() for text in texts]
        bm25 = BM25Okapi(tokenized_texts)
        sparse_vectors = []

        for doc in tokenized_texts:
            # Get BM25 scores for all terms
            scores = bm25.get_scores(doc)
            # Convert scores to list of floats and ensure correct dimension
            sparse_vec = [float(score) for score in scores]
            # Pad or truncate to match expected dimension
            if len(sparse_vec) < 30:
                sparse_vec.extend([0.0] * (30 - len(sparse_vec)))
            else:
                sparse_vec = sparse_vec[:30]
            sparse_vectors.append(sparse_vec)

        # Create points for Qdrant
        points = []
        for doc_id, text, dense_emb, sparse_vec, doc in zip(
            doc_ids, texts, dense_embeddings, sparse_vectors, docs
        ):
            points.append(
                models.PointStruct(
                    id=doc_id,
                    vector={
                        "dense": dense_emb,
                        "sparse": sparse_vec,
                    },
                    payload={
                        "text": text,
                        "metadata": doc.metadata,
                    },
                )
            )

        try:
            # Upsert points to Qdrant
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
        except Exception as e:
            logger.error(f"Error indexing documents: {e}")
            raise

    @measure_execution_time
    def hybrid_search(
        self, query: str, limit: int = 15, alpha: float = 0.5
    ) -> List[SearchResult]:
        """
        Perform hybrid search using both dense and sparse vectors.

        Args:
            query: Search query string
            limit: Number of results to return
            alpha: Weight between dense (1.0) and sparse (0.0) search. Default 0.5 for equal weighting.

        Returns:
            List of SearchResult objects
        """
        # Generate dense embedding for query
        query_embedding = self.embeddings.embed_query(query)

        # Generate sparse embedding for query
        query_tokens = query.lower().split()
        tokenized_texts = [query_tokens]
        bm25 = BM25Okapi(tokenized_texts)
        query_sparse_vector = [float(score) for score in bm25.get_scores(query_tokens)]
        # Ensure query sparse vector has correct dimension
        if len(query_sparse_vector) < 30:
            query_sparse_vector.extend([0.0] * (30 - len(query_sparse_vector)))
        else:
            query_sparse_vector = query_sparse_vector[:30]

        try:
            # Get results from both dense and sparse searches
            # Reranking will be done once on combined results in chat_session_manager.py

            # Dense search
            dense_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_embedding,
                query_filter=None,
                limit=limit * 2,  # Get more results to ensure good coverage
                with_payload=True,
                with_vectors=False,
                score_threshold=0.0,
                using="dense",  # Specify vector name
            )
            dense_results = dense_response.points

            # Sparse search
            sparse_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_sparse_vector,
                query_filter=None,
                limit=limit * 2,  # Get more results to ensure good coverage
                with_payload=True,
                with_vectors=False,
                score_threshold=0.0,
                using="sparse",  # Specify vector name
            )
            sparse_results = sparse_response.points

            # Combine all unique results with hybrid scoring
            all_results = []
            seen_texts = set()

            # Add results from both searches
            self._add_search_results(
                search_results=dense_results,
                source="dense",
                all_results=all_results,
                seen_texts=seen_texts,
            )
            self._add_search_results(
                search_results=sparse_results,
                source="sparse",
                all_results=all_results,
                seen_texts=seen_texts,
            )

            # Sort by score and return top results
            # Reranking will happen once in chat_session_manager.py after combining both query results
            all_results.sort(key=lambda x: x["score"], reverse=True)
            top_results = all_results[:limit]

            # Extract and format results
            return [
                SearchResult(
                    text=result["payload"]["text"],
                    metadata=result["payload"]["metadata"],
                    score=result["score"],
                )
                for result in top_results
            ]

        except Exception as e:
            logger.error(f"Error during hybrid search: {e}")
            raise

    def _add_search_results(
        self,
        search_results: List,
        source: str,
        all_results: List[Dict],
        seen_texts: set,
    ) -> None:
        for hit in search_results:
            if hit.payload and hit.payload.get("text"):
                text = hit.payload["text"]
                if text not in seen_texts:
                    all_results.append(
                        {
                            "payload": hit.payload,
                            "score": hit.score,
                            "source": source,
                        }
                    )
                    seen_texts.add(text)


# Example usage
if __name__ == "__main__":
    # This code only runs when the file is executed directly, not when imported
    indexer = QdrantIndexer()

    # Example search
    results = indexer.hybrid_search("product recommendation", limit=5)
    for result in results:
        print(f"Score: {result.score} - {result.text[:100]}...")
