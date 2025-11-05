"""
Retrieval evaluation metrics for information retrieval systems.

Implements standard metrics:
- Recall@K: Proportion of relevant documents in top K results
- nDCG@K: Normalized Discounted Cumulative Gain for ranking quality
- MRR: Mean Reciprocal Rank of first relevant document
- Hit Rate@K: Binary indicator of whether any relevant doc appears in top K
"""

import logging
import math
from typing import List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger()


@dataclass
class RetrievalMetricsResult:
    """Results from retrieval metrics computation."""

    recall_at_5: float
    recall_at_10: float
    recall_at_15: float
    ndcg_at_5: float
    ndcg_at_10: float
    ndcg_at_15: float
    mrr: float
    hit_rate_at_5: float
    hit_rate_at_10: float
    hit_rate_at_15: float
    num_relevant_docs: int
    num_retrieved_docs: int


class RetrievalMetrics:
    """
    Compute information retrieval metrics using reranker scores as ground truth.

    The reranker provides relevance scores (0-1) for documents, which we use
    as pseudo ground truth to evaluate the initial retrieval stage.
    """

    def __init__(self, relevance_threshold: float = 0.5):
        """
        Initialize retrieval metrics calculator.

        Args:
            relevance_threshold: Score threshold above which a document is considered relevant.
                                Default 0.5 (documents with reranker score >= 0.5 are relevant)
        """
        self.relevance_threshold = relevance_threshold

    def compute_all_metrics(
        self,
        retrieved_docs: List[Dict[str, Any]],
        reranker_judgments: List[Dict[str, Any]],
        k_values: List[int] = [5, 10, 15],
    ) -> RetrievalMetricsResult:
        """
        Compute all retrieval metrics.

        Args:
            retrieved_docs: List of documents from retrieval stage with their metadata
                           Format: [{"text": str, "score": float, "doc_id": str}, ...]
            reranker_judgments: Reranker relevance scores for documents
                               Format: [{"doc_id": str, "relevance_score": float}, ...]
            k_values: List of K values to compute metrics at (default: [5, 10, 15])

        Returns:
            RetrievalMetricsResult with all computed metrics
        """
        if not retrieved_docs or not reranker_judgments:
            logger.warning(
                "Empty retrieved_docs or reranker_judgments, returning zero metrics"
            )
            return self._zero_metrics()

        # Build relevance map: doc_id -> relevance_score
        relevance_map = {
            judgment["doc_id"]: judgment["relevance_score"]
            for judgment in reranker_judgments
        }

        # Get relevance scores for retrieved docs in retrieval order
        retrieved_relevances = []
        for doc in retrieved_docs:
            doc_id = self._get_doc_id(doc)
            relevance_score = relevance_map.get(doc_id, 0.0)
            retrieved_relevances.append(relevance_score)

        # Binary relevance labels (for recall, hit rate)
        binary_relevance = [
            1 if score >= self.relevance_threshold else 0
            for score in retrieved_relevances
        ]

        # Count total relevant documents (according to reranker)
        num_relevant = sum(
            1 for score in relevance_map.values() if score >= self.relevance_threshold
        )

        # Compute metrics at different K values
        metrics = {}
        for k in k_values:
            metrics[f"recall_at_{k}"] = self.recall_at_k(
                binary_relevance, num_relevant, k
            )
            metrics[f"ndcg_at_{k}"] = self.ndcg_at_k(retrieved_relevances, k)
            metrics[f"hit_rate_at_{k}"] = self.hit_rate_at_k(binary_relevance, k)

        # MRR is computed once (not K-specific)
        metrics["mrr"] = self.mrr(binary_relevance)

        return RetrievalMetricsResult(
            recall_at_5=metrics.get("recall_at_5", 0.0),
            recall_at_10=metrics.get("recall_at_10", 0.0),
            recall_at_15=metrics.get("recall_at_15", 0.0),
            ndcg_at_5=metrics.get("ndcg_at_5", 0.0),
            ndcg_at_10=metrics.get("ndcg_at_10", 0.0),
            ndcg_at_15=metrics.get("ndcg_at_15", 0.0),
            mrr=metrics["mrr"],
            hit_rate_at_5=metrics.get("hit_rate_at_5", 0.0),
            hit_rate_at_10=metrics.get("hit_rate_at_10", 0.0),
            hit_rate_at_15=metrics.get("hit_rate_at_15", 0.0),
            num_relevant_docs=num_relevant,
            num_retrieved_docs=len(retrieved_docs),
        )

    def recall_at_k(
        self, binary_relevance: List[int], num_relevant: int, k: int
    ) -> float:
        """
        Compute Recall@K: proportion of relevant docs retrieved in top K.

        Recall@K = (# relevant docs in top K) / (total # relevant docs)

        Args:
            binary_relevance: Binary relevance labels (1=relevant, 0=not relevant) in retrieval order
            num_relevant: Total number of relevant documents in collection
            k: Number of top results to consider

        Returns:
            Recall@K score (0.0 to 1.0)
        """
        if num_relevant == 0:
            return 0.0

        top_k_relevance = binary_relevance[:k]
        num_relevant_retrieved = sum(top_k_relevance)

        return num_relevant_retrieved / num_relevant

    def ndcg_at_k(self, relevance_scores: List[float], k: int) -> float:
        """
        Compute Normalized Discounted Cumulative Gain at K.

        nDCG accounts for both relevance and position, with higher-ranked
        relevant documents contributing more to the score.

        DCG@K = sum(rel_i / log2(i+1)) for i in 1..K
        IDCG@K = DCG of perfect ranking (sorted by relevance)
        nDCG@K = DCG@K / IDCG@K

        Args:
            relevance_scores: Graded relevance scores (0-1) in retrieval order
            k: Number of top results to consider

        Returns:
            nDCG@K score (0.0 to 1.0)
        """
        if not relevance_scores or k <= 0:
            return 0.0

        # Compute DCG@K for retrieved ranking
        dcg = self._compute_dcg(relevance_scores[:k])

        # Compute Ideal DCG (IDCG) - best possible ranking
        ideal_relevance = sorted(relevance_scores, reverse=True)
        idcg = self._compute_dcg(ideal_relevance[:k])

        if idcg == 0.0:
            return 0.0

        return dcg / idcg

    def _compute_dcg(self, relevance_scores: List[float]) -> float:
        """
        Compute Discounted Cumulative Gain.

        DCG = sum(rel_i / log2(i+1)) for i in positions
        """
        dcg = 0.0
        for i, rel in enumerate(relevance_scores, start=1):
            # Discount factor: 1 / log2(position + 1)
            discount = math.log2(i + 1)
            dcg += rel / discount

        return dcg

    def mrr(self, binary_relevance: List[int]) -> float:
        """
        Compute Mean Reciprocal Rank (for single query).

        MRR = 1 / rank_of_first_relevant_doc

        Useful for tasks where only the first relevant result matters
        (e.g., question answering, navigation).

        Args:
            binary_relevance: Binary relevance labels in retrieval order

        Returns:
            Reciprocal rank (0.0 to 1.0)
        """
        for i, rel in enumerate(binary_relevance, start=1):
            if rel == 1:
                return 1.0 / i

        # No relevant document found
        return 0.0

    def hit_rate_at_k(self, binary_relevance: List[int], k: int) -> float:
        """
        Compute Hit Rate@K: binary indicator of success.

        Hit Rate@K = 1 if any relevant doc in top K, else 0

        Simple metric that only cares about whether we found something relevant,
        not how many or how well ranked.

        Args:
            binary_relevance: Binary relevance labels in retrieval order
            k: Number of top results to consider

        Returns:
            1.0 if hit, 0.0 if miss
        """
        top_k_relevance = binary_relevance[:k]
        return 1.0 if any(top_k_relevance) else 0.0

    def _get_doc_id(self, doc: Dict[str, Any]) -> str:
        """
        Extract document ID from document dict.

        Tries multiple fields to find a unique identifier.
        Falls back to using the document text as ID if no explicit ID found.
        """
        # Try common ID fields
        if "doc_id" in doc:
            return doc["doc_id"]
        if "id" in doc:
            return doc["id"]
        if "metadata" in doc and "id" in doc["metadata"]:
            return doc["metadata"]["id"]

        # Fallback: use text content as identifier
        return doc.get("text", "")

    def _zero_metrics(self) -> RetrievalMetricsResult:
        """Return all-zero metrics for error cases."""
        return RetrievalMetricsResult(
            recall_at_5=0.0,
            recall_at_10=0.0,
            recall_at_15=0.0,
            ndcg_at_5=0.0,
            ndcg_at_10=0.0,
            ndcg_at_15=0.0,
            mrr=0.0,
            hit_rate_at_5=0.0,
            hit_rate_at_10=0.0,
            hit_rate_at_15=0.0,
            num_relevant_docs=0,
            num_retrieved_docs=0,
        )
