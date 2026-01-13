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
import re
import time

logger = setup_logger(__name__)

# Common English stopwords to filter out
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "been",
        "be",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "what",
        "which",
        "who",
        "whom",
        "their",
        "my",
        "your",
        "his",
        "her",
        "our",
        "not",
        "no",
        "nor",
        "so",
        "if",
        "then",
        "else",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "any",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "also",
        "now",
        "here",
        "there",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "once",
        "up",
        "down",
        "out",
        "off",
        "over",
        "am",
        "being",
        "because",
        "until",
        "while",
    ]
)


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

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text with punctuation removal and stopword filtering."""
        # Convert to lowercase and extract alphanumeric tokens
        tokens = re.findall(r"\b[a-z0-9]+\b", text.lower())
        # Filter out stopwords and very short tokens
        return [t for t in tokens if t not in STOPWORDS and len(t) > 1]

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
                    },
                    sparse_vectors_config={
                        "sparse": models.SparseVectorParams(
                            modifier=models.Modifier.IDF,
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
        tokenized_texts = [self._tokenize(text) for text in texts]

        # Build vocabulary: get all unique terms across all documents
        vocabulary: Set[str] = set()
        for tokens in tokenized_texts:
            vocabulary.update(tokens)

        # Create vocabulary index mapping (all unique terms, sorted for consistency)
        vocabulary_list = sorted(list(vocabulary))
        vocabulary_indices = {term: idx for idx, term in enumerate(vocabulary_list)}

        # Compute document frequency for each term
        doc_freq: Dict[str, int] = {}
        for tokens in tokenized_texts:
            for term in set(tokens):
                doc_freq[term] = doc_freq.get(term, 0) + 1

        # Compute IDF for each term using standard formula
        num_docs = len(tokenized_texts)
        idf: Dict[str, float] = {}
        for term, df in doc_freq.items():
            idf[term] = math.log((num_docs + 1) / (df + 1)) + 1.0

        # Generate sparse vectors for each document using TF-IDF
        # Using proper SparseVector format with indices and values
        sparse_vectors: List[models.SparseVector] = []
        for tokens in tokenized_texts:
            term_freq = Counter(tokens)
            doc_length = len(tokens)

            indices: List[int] = []
            values: List[float] = []

            for term, count in term_freq.items():
                if term in vocabulary_indices:
                    idx = vocabulary_indices[term]
                    # TF normalized by document length, multiplied by IDF
                    tf = count / doc_length
                    tfidf_score = tf * idf[term]
                    indices.append(idx)
                    values.append(float(tfidf_score))

            sparse_vectors.append(models.SparseVector(indices=indices, values=values))

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

    def _retry_operation(self, operation, max_retries=5, base_delay=2.0):
        """Retry an operation with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return operation()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                delay = base_delay * (2**attempt)
                logger.warning(
                    f"Operation failed (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)

    def rebuild_sparse_vectors_only(self) -> None:
        """
        Rebuild only sparse vectors without regenerating dense embeddings.
        This fetches existing documents, regenerates sparse vectors, and re-uploads.
        Skips OpenAI API calls entirely - costs $0.
        """
        logger.info("Starting sparse vector rebuild (preserving dense vectors)...")

        # Create a client with longer timeout for large data transfers
        long_timeout_client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key,
            timeout=300.0,  # 5 minutes timeout
        )

        # Step 1: Fetch all existing points with vectors and payloads
        all_points = []
        offset = None

        while True:
            scroll_result = self._retry_operation(
                lambda: long_timeout_client.scroll(
                    collection_name=self.collection_name,
                    limit=100,  # Smaller batches to avoid timeout
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
            )

            points, next_offset = scroll_result
            all_points.extend(points)
            if len(all_points) % 1000 == 0:
                logger.info(f"Fetched {len(all_points)} points so far...")

            if next_offset is None or len(points) == 0:
                break
            offset = next_offset

        if not all_points:
            logger.warning("No documents found in collection")
            return

        logger.info(f"Total points fetched: {len(all_points)}")

        # Step 2: Extract texts and build vocabulary
        texts = []
        for point in all_points:
            if point.payload and point.payload.get("text"):
                texts.append(point.payload["text"])
            else:
                texts.append("")

        tokenized_texts = [self._tokenize(text) for text in texts]

        # Build vocabulary from all documents
        vocabulary: Set[str] = set()
        for tokens in tokenized_texts:
            vocabulary.update(tokens)

        vocabulary_list = sorted(list(vocabulary))
        vocabulary_indices = {term: idx for idx, term in enumerate(vocabulary_list)}

        # Compute document frequency and IDF
        doc_freq: Dict[str, int] = {}
        for tokens in tokenized_texts:
            for term in set(tokens):
                doc_freq[term] = doc_freq.get(term, 0) + 1

        num_docs = len(tokenized_texts)
        idf: Dict[str, float] = {}
        for term, df in doc_freq.items():
            idf[term] = math.log((num_docs + 1) / (df + 1)) + 1.0

        # Store for query-time use
        self._vocabulary_indices = vocabulary_indices
        self._idf = idf

        logger.info(f"Built vocabulary with {len(vocabulary_indices)} terms")

        # Step 3: Delete and recreate collection with correct sparse config
        logger.info("Recreating collection with correct sparse vector config...")
        self.client.delete_collection(self.collection_name)
        self.create_index()

        # Step 4: Generate new sparse vectors and re-upload with existing dense vectors
        new_points = []
        for i, (point, tokens) in enumerate(zip(all_points, tokenized_texts)):
            # Get existing dense vector
            dense_vector = (
                point.vector.get("dense")
                if isinstance(point.vector, dict)
                else point.vector
            )

            # Generate new sparse vector
            term_freq = Counter(tokens)
            doc_length = len(tokens) if tokens else 1

            indices: List[int] = []
            values: List[float] = []

            for term, count in term_freq.items():
                if term in vocabulary_indices:
                    idx = vocabulary_indices[term]
                    tf = count / doc_length
                    tfidf_score = tf * idf[term]
                    indices.append(idx)
                    values.append(float(tfidf_score))

            sparse_vector = models.SparseVector(indices=indices, values=values)

            new_points.append(
                models.PointStruct(
                    id=point.id,
                    vector={
                        "dense": dense_vector,
                        "sparse": sparse_vector,
                    },
                    payload=point.payload,
                )
            )

            # Batch upsert every 100 points
            if len(new_points) >= 100:
                batch_to_upload = new_points.copy()
                self._retry_operation(
                    lambda: long_timeout_client.upsert(
                        collection_name=self.collection_name,
                        points=batch_to_upload,
                    )
                )
                logger.info(f"Uploaded {i + 1}/{len(all_points)} points")
                new_points = []

        # Upload remaining points
        if new_points:
            batch_to_upload = new_points.copy()
            self._retry_operation(
                lambda: long_timeout_client.upsert(
                    collection_name=self.collection_name,
                    points=batch_to_upload,
                )
            )

        logger.info(
            f"Sparse vector rebuild complete. Processed {len(all_points)} documents."
        )

    def _generate_query_sparse_vector(self, query: str) -> models.SparseVector:
        """Generate a sparse vector for the query using stored vocabulary and IDF."""
        if not self._vocabulary_indices or not self._idf:
            self._rebuild_vocabulary_from_collection()

        tokens = self._tokenize(query)
        term_freq = Counter(tokens)
        query_length = len(tokens)

        indices: List[int] = []
        values: List[float] = []

        for term, count in term_freq.items():
            if term in self._vocabulary_indices and term in self._idf:
                idx = self._vocabulary_indices[term]
                tf = count / query_length
                tfidf_score = tf * self._idf[term]
                indices.append(idx)
                values.append(float(tfidf_score))

        return models.SparseVector(indices=indices, values=values)

    def _rebuild_vocabulary_from_collection(self) -> None:
        """Rebuild vocabulary and IDF from existing documents in the collection."""
        try:
            # Retrieve documents using pagination for larger collections
            texts = []
            offset = None
            max_docs = 10000  # Increased from 1000 for better vocabulary coverage

            while len(texts) < max_docs:
                scroll_result = self.client.scroll(
                    collection_name=self.collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )

                points, next_offset = scroll_result
                for point in points:
                    if point.payload and point.payload.get("text"):
                        texts.append(point.payload["text"])

                if next_offset is None or len(points) == 0:
                    break
                offset = next_offset

            if not texts:
                logger.warning("No documents found in collection to rebuild vocabulary")
                return

            # Build vocabulary from retrieved documents
            tokenized_texts = [self._tokenize(text) for text in texts]
            vocabulary: Set[str] = set()
            for tokens in tokenized_texts:
                vocabulary.update(tokens)

            # Create vocabulary index mapping (sorted for consistency)
            vocabulary_list = sorted(list(vocabulary))
            self._vocabulary_indices = {
                term: idx for idx, term in enumerate(vocabulary_list)
            }

            # Compute document frequency for each term
            doc_freq: Dict[str, int] = {}
            for tokens in tokenized_texts:
                for term in set(tokens):
                    doc_freq[term] = doc_freq.get(term, 0) + 1

            # Compute IDF for each term
            num_docs = len(tokenized_texts)
            self._idf = {}
            for term, df in doc_freq.items():
                self._idf[term] = math.log((num_docs + 1) / (df + 1)) + 1.0

            logger.info(
                f"Rebuilt vocabulary with {len(self._vocabulary_indices)} terms from {len(texts)} documents"
            )
        except Exception as e:
            logger.error(f"Error rebuilding vocabulary: {e}")
            # Don't raise - allow fallback to work

    @measure_execution_time
    def hybrid_search(
        self, query: str, limit: int = 15, alpha: float = 0.5, rrf_k: int = 60
    ) -> List[SearchResult]:
        """
        Perform hybrid search using Reciprocal Rank Fusion (RRF).

        Args:
            query: Search query string
            limit: Number of results to return
            alpha: Weight for dense vs sparse. 1.0 = dense only, 0.0 = sparse only.
            rrf_k: RRF constant (default 60). Higher values reduce impact of rank differences.

        Returns:
            List of SearchResult objects with RRF-fused scores
        """

        query_embedding = self.embeddings.embed_query(query)
        query_sparse_vector = self._generate_query_sparse_vector(query)

        # Fetch more candidates for better fusion
        fetch_limit = limit * 3

        try:
            # Dense search
            dense_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_embedding,
                query_filter=None,
                limit=fetch_limit,
                with_payload=True,
                with_vectors=False,
                score_threshold=0.0,
                using="dense",
            )
            dense_results = dense_response.points

            # Sparse search
            sparse_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_sparse_vector,
                query_filter=None,
                limit=fetch_limit,
                with_payload=True,
                with_vectors=False,
                using="sparse",
            )
            sparse_results = sparse_response.points

            # Build RRF scores using document ID as key
            # RRF formula: score = 1 / (k + rank)
            rrf_scores: Dict[str, Dict[str, Any]] = {}

            # Process dense results
            for rank, hit in enumerate(dense_results, start=1):
                if not hit.payload or not hit.payload.get("text"):
                    continue
                doc_id = str(hit.id)
                rrf_dense = 1.0 / (rrf_k + rank)
                rrf_scores[doc_id] = {
                    "payload": hit.payload,
                    "rrf_dense": rrf_dense,
                    "rrf_sparse": 0.0,
                }

            # Process sparse results
            for rank, hit in enumerate(sparse_results, start=1):
                if not hit.payload or not hit.payload.get("text"):
                    continue
                doc_id = str(hit.id)
                rrf_sparse = 1.0 / (rrf_k + rank)
                if doc_id in rrf_scores:
                    rrf_scores[doc_id]["rrf_sparse"] = rrf_sparse
                else:
                    rrf_scores[doc_id] = {
                        "payload": hit.payload,
                        "rrf_dense": 0.0,
                        "rrf_sparse": rrf_sparse,
                    }

            # Compute final weighted RRF score
            # final_score = alpha * rrf_dense + (1 - alpha) * rrf_sparse
            results = []
            for doc_id, data in rrf_scores.items():
                final_score = (
                    alpha * data["rrf_dense"] + (1 - alpha) * data["rrf_sparse"]
                )
                results.append(
                    {
                        "doc_id": doc_id,
                        "payload": data["payload"],
                        "score": final_score,
                        "rrf_dense": data["rrf_dense"],
                        "rrf_sparse": data["rrf_sparse"],
                    }
                )

            # Sort by final RRF score
            results.sort(key=lambda x: x["score"], reverse=True)
            top_results = results[:limit]

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
