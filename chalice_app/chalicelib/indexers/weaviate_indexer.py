import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr
from weaviate.auth import AuthApiKey
from weaviate.client import Client

from chalicelib.core.config import config
from chalicelib.core.logger_config import setup_logger
from chalicelib.core.performance_timer import measure_execution_time

logger = setup_logger(__name__)

# Import Metadata from reddit_chunker_indexer using absolute import


@dataclass
class SearchResult:
    text: str
    metadata: Dict[str, Any]
    score: float = 0.0


class WeaviateIndexer:
    def __init__(self):
        self.client = Client(
            url=config.weaviate_config.weaviate_url,
            auth_client_secret=AuthApiKey(
                api_key=config.weaviate_config.weaviate_api_key
            ),
        )
        self.embeddings = OpenAIEmbeddings(
            api_key=SecretStr(config.openai_api_key),
            model="text-embedding-3-small",  # This is the default model
        )

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

        # Note: Weaviate's hybrid search handles BM25 internally via with_hybrid()
        # so we don't need to generate sparse vectors manually

        # Create documents
        documents = [
            {
                "id": doc_id,
                "text": text,
                "vector": dense_emb,
                "metadata": doc.metadata,
            }
            for doc_id, text, dense_emb, doc in zip(
                doc_ids, texts, dense_embeddings, docs
            )
        ]

        try:
            batch = self.client.batch()
            for doc in documents:
                properties = {
                    "text": doc["text"],
                    "metadata": doc["metadata"],
                }

                batch.add_data_object(
                    data_object=properties,
                    class_name="RedditPost",
                    uuid=doc["id"],
                    vector=doc["vector"],
                )
            batch.flush()
        finally:
            pass

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

        # Execute search
        try:
            results = (
                self.client.query.get(
                    class_name="RedditPost",
                    properties=["text", "metadata {post_id subreddit_name type}"],
                )
                .with_hybrid(
                    query=query,
                    vector=query_embedding,
                    alpha=alpha,
                    properties=["text"],
                )
                .with_limit(limit)
                .do()
            )

            # Extract and format results
            if results and "data" in results and "Get" in results["data"]:
                documents = results["data"]["Get"]["RedditPost"]
                return [
                    SearchResult(
                        text=doc["text"],
                        metadata=doc["metadata"],
                        score=doc.get("_additional", {}).get("score", 0.0),
                    )
                    for doc in documents
                ]
            return []

        except Exception as e:
            logger.error(f"Error during hybrid search: {e}")
            return []


# Example usage
if __name__ == "__main__":
    # This code only runs when the file is executed directly, not when imported
    indexer = WeaviateIndexer()

    # Example search
    results = indexer.hybrid_search("product recommendation", limit=5)
    for result in results:
        print(f"Score: {result.score} - {result.text[:100]}...")
