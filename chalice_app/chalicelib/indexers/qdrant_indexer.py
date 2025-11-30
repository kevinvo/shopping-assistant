from langchain_openai import OpenAIEmbeddings
from typing import List, Dict, Any, Set
from langchain.schema import Document
from dataclasses import dataclass
from collections import Counter
from chalicelib.core.config import config
from chalicelib.core.logger_config import setup_logger
from qdrant_client import QdrantClient
from qdrant_client.http import models
import uuid
from chalicelib.core.performance_timer import measure_execution_time
from pydantic import SecretStr
import math

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
            model="text-embedding-3-small",
        )
        self.collection_name = "reddit_posts"
        # Store vocabulary and IDF for query-time sparse vector generation
        # This will be populated when documents are indexed
        self._vocabulary_indices: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}

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
                            size=1536,
                            distance=models.Distance.COSINE,
                        ),
                        "sparse": models.VectorParams(
                            size=30,
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

        dense_embeddings: List[List[float]] = self.embeddings.embed_documents(texts)

        # Build vocabulary and compute IDF for term-based sparse vectors
        tokenized_texts = [text.lower().split() for text in texts]

        # Build vocabulary: get all unique terms across all documents
        vocabulary: Set[str] = set()
        for tokens in tokenized_texts:
            vocabulary.update(tokens)
        vocabulary_list = sorted(list(vocabulary))  # Sort for consistent ordering

        # Limit vocabulary to top terms by document frequency (for 30-dim sparse vector)
        doc_freq = Counter()
        for tokens in tokenized_texts:
            doc_freq.update(set(tokens))

        # Get top 30 terms by document frequency (most common terms)
        top_terms = [term for term, _ in doc_freq.most_common(30)]
        if len(top_terms) < 30:
            # If we have fewer than 30 unique terms, pad with remaining vocabulary
            remaining = [t for t in vocabulary_list if t not in top_terms]
            top_terms.extend(remaining[: 30 - len(top_terms)])
        vocabulary_indices = {term: idx for idx, term in enumerate(top_terms[:30])}

        # Compute IDF for each term
        num_docs = len(tokenized_texts)
        idf = {}
        for term in vocabulary_indices.keys():
            df = sum(1 for tokens in tokenized_texts if term in tokens)
            idf[term] = math.log((num_docs - df + 0.5) / (df + 0.5) + 1.0)

        # Generate sparse vectors for each document using TF-IDF
        sparse_vectors = []
        for tokens in tokenized_texts:
            # Compute term frequencies
            term_freq = Counter(tokens)
            doc_length = len(tokens)

            # Build sparse vector: TF-IDF for each term in vocabulary
            sparse_vec = [0.0] * 30
            for term, idx in vocabulary_indices.items():
                if term in term_freq:
                    # TF (term frequency) normalized by document length
                    tf = term_freq[term] / doc_length
                    # TF-IDF score
                    sparse_vec[idx] = float(tf * idf[term])

            sparse_vectors.append(sparse_vec)

        # Store vocabulary and IDF for query-time use
        self._vocabulary_indices = vocabulary_indices
        self._idf = idf

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
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
            logger.info(f"Successfully saved {len(points)} documents to Qdrant")
        except Exception as e:
            logger.error(f"Error indexing documents: {e}")
            raise

    def _rebuild_vocabulary_from_collection(self) -> None:
        """Rebuild vocabulary and IDF from existing documents in the collection."""
        try:
            # Retrieve a sample of documents to build vocabulary
            # We'll use scroll to get all documents, but limit to reasonable number
            scroll_result = self.client.scroll(
                collection_name=self.collection_name,
                limit=1000,  # Sample up to 1000 documents
                with_payload=True,
                with_vectors=False,
            )

            texts = []
            for point in scroll_result[0]:
                if point.payload and point.payload.get("text"):
                    texts.append(point.payload["text"])

            if not texts:
                logger.warning("No documents found in collection to rebuild vocabulary")
                return

            # Build vocabulary from retrieved documents
            tokenized_texts = [text.lower().split() for text in texts]
            vocabulary: Set[str] = set()
            for tokens in tokenized_texts:
                vocabulary.update(tokens)

            doc_freq = Counter()
            for tokens in tokenized_texts:
                doc_freq.update(set(tokens))

            top_terms = [term for term, _ in doc_freq.most_common(30)]
            vocabulary_list = sorted(list(vocabulary))
            if len(top_terms) < 30:
                remaining = [t for t in vocabulary_list if t not in top_terms]
                top_terms.extend(remaining[: 30 - len(top_terms)])

            self._vocabulary_indices = {
                term: idx for idx, term in enumerate(top_terms[:30])
            }

            num_docs = len(tokenized_texts)
            self._idf = {}
            for term in self._vocabulary_indices.keys():
                df = sum(1 for tokens in tokenized_texts if term in tokens)
                self._idf[term] = math.log((num_docs - df + 0.5) / (df + 0.5) + 1.0)

            logger.info(
                f"Rebuilt vocabulary with {len(self._vocabulary_indices)} terms from {len(texts)} documents"
            )
        except Exception as e:
            logger.error(f"Error rebuilding vocabulary: {e}")
            # Don't raise - allow fallback to work

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

        query_embedding = self.embeddings.embed_query(query)

        # Sparse search is temporarily disabled
        # TODO: Re-enable sparse vector generation after fixing the implementation
        # query_sparse_vector = [0.0] * 30

        try:
            # Sparse search is temporarily disabled - using dense search only
            # TODO: Re-enable sparse search after fixing sparse vector generation

            dense_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_embedding,
                query_filter=None,
                limit=limit,
                with_payload=True,
                with_vectors=False,
                score_threshold=0.0,
                using="dense",
            )

            dense_results = dense_response.points
            # Sparse search disabled - sparse_results not needed

            all_results = []
            seen_texts = set()

            self._add_search_results(
                search_results=dense_results,
                source="dense",
                all_results=all_results,
                seen_texts=seen_texts,
            )
            # Sparse search disabled
            # self._add_search_results(
            #     search_results=sparse_results,
            #     source="sparse",
            #     all_results=all_results,
            #     seen_texts=seen_texts,
            # )

            all_results.sort(key=lambda x: x["score"], reverse=True)
            top_results = all_results[:limit]

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
