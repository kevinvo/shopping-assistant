#!/usr/bin/env python3
"""Test suite for retrieval metrics implementation.

Run with: pytest chalice_app/tests/test_retrieval_metrics.py -v
"""
import pytest

from chalicelib.retrieval_metrics import RetrievalMetrics
from dataclasses import asdict, dataclass


@dataclass
class RetrievedDoc:
    doc_id: str
    text: str
    score: float


@dataclass
class RerankerJudgment:
    doc_id: str
    relevance_score: float


def _doc(doc_id: str, text: str, score: float) -> dict:
    return asdict(RetrievedDoc(doc_id=doc_id, text=text, score=score))


def _judgment(doc_id: str, relevance_score: float) -> dict:
    return asdict(RerankerJudgment(doc_id=doc_id, relevance_score=relevance_score))


@pytest.mark.unit
def test_basic_metrics():
    """Test basic retrieval metrics with a simple example."""

    # Mock retrieved documents (before reranking)
    retrieved_docs = [
        _doc(doc_id="doc1", text="Great headphones for music", score=0.9),
        _doc(doc_id="doc2", text="Budget gaming laptop", score=0.85),
        _doc(doc_id="doc3", text="Wireless mouse review", score=0.8),
        _doc(doc_id="doc4", text="Mechanical keyboard guide", score=0.75),
        _doc(doc_id="doc5", text="Monitor stand recommendations", score=0.7),
        _doc(doc_id="doc6", text="USB cable comparison", score=0.65),
        _doc(doc_id="doc7", text="Desk lamp options", score=0.6),
    ]

    # Mock reranker judgments (relevance scores from reranker)
    reranker_judgments = [
        _judgment(doc_id="doc1", relevance_score=0.95),
        _judgment(doc_id="doc2", relevance_score=0.85),
        _judgment(doc_id="doc3", relevance_score=0.3),
        _judgment(doc_id="doc4", relevance_score=0.7),
        _judgment(doc_id="doc5", relevance_score=0.2),
        _judgment(doc_id="doc6", relevance_score=0.1),
        _judgment(doc_id="doc7", relevance_score=0.05),
    ]

    # Initialize metrics calculator
    metrics = RetrievalMetrics(relevance_threshold=0.5)

    # Compute all metrics (using K=[5, 10, 15] as per plan)
    result = metrics.compute_all_metrics(
        retrieved_docs=retrieved_docs,
        reranker_judgments=reranker_judgments,
        k_values=[5, 10, 15],
    )

    print("=" * 60)
    print("RETRIEVAL METRICS TEST RESULTS")
    print("=" * 60)
    print("\nScenario: 7 docs retrieved, 3 judged as relevant by reranker")
    print("Relevant docs (score >= 0.5): doc1, doc2, doc4")
    print("\nRetrieval order: doc1, doc2, doc3, doc4, doc5, doc6, doc7")
    print("Reranker preferences: doc1 (0.95), doc2 (0.85), doc4 (0.7)")

    print("\n" + "-" * 60)
    print("RECALL METRICS")
    print("-" * 60)
    print(f"Recall@5:  {result.recall_at_5:.3f} (found 3/3 relevant docs in top 5)")
    print(
        f"Recall@10: {result.recall_at_10:.3f} (found 3/3 relevant docs in top 7, but K=10)"
    )
    print(
        f"Recall@15: {result.recall_at_15:.3f} (found 3/3 relevant docs in all 7 docs)"
    )

    print("\n" + "-" * 60)
    print("nDCG METRICS (Ranking Quality)")
    print("-" * 60)
    print(f"nDCG@5:    {result.ndcg_at_5:.3f}")
    print(f"nDCG@10:   {result.ndcg_at_10:.3f}")
    print(f"nDCG@15:   {result.ndcg_at_15:.3f}")

    print("\n" + "-" * 60)
    print("OTHER METRICS")
    print("-" * 60)
    print(f"MRR:       {result.mrr:.3f} (first relevant doc at position 1)")
    print(f"Hit@5:     {result.hit_rate_at_5:.3f}")
    print(f"Hit@10:    {result.hit_rate_at_10:.3f}")
    print(f"Hit@15:    {result.hit_rate_at_15:.3f}")

    print("\n" + "-" * 60)
    print("SUMMARY")
    print("-" * 60)
    print(f"Total relevant docs:   {result.num_relevant_docs}")
    print(f"Total retrieved docs:  {result.num_retrieved_docs}")

    print("\n✅ Test completed successfully!\n")
    print("=" * 60)

    # Pytest assertions
    assert result.recall_at_5 == 1.0, "Should find all relevant docs in top 5"
    assert result.mrr == 1.0, "First doc is relevant, MRR should be 1.0"
    assert result.hit_rate_at_5 == 1.0, "Should have at least one relevant doc"


@pytest.mark.unit
def test_perfect_retrieval():
    """Test case where retrieval perfectly matches reranker preferences."""

    print("\n\nTEST: Perfect Retrieval")
    print("=" * 60)

    retrieved_docs = [
        _doc(doc_id="doc1", text="Perfect match", score=1.0),
        _doc(doc_id="doc2", text="Good match", score=0.9),
        _doc(doc_id="doc3", text="Okay match", score=0.8),
    ]

    reranker_judgments = [
        _judgment(doc_id="doc1", relevance_score=1.0),
        _judgment(doc_id="doc2", relevance_score=0.9),
        _judgment(doc_id="doc3", relevance_score=0.8),
    ]

    metrics = RetrievalMetrics(relevance_threshold=0.5)
    result = metrics.compute_all_metrics(
        retrieved_docs=retrieved_docs,
        reranker_judgments=reranker_judgments,
        k_values=[5, 10, 15],
    )

    print(f"Recall@5:  {result.recall_at_5:.3f} (should be 1.0)")
    print(f"nDCG@5:    {result.ndcg_at_5:.3f} (should be 1.0 - perfect ranking)")
    print(f"MRR:       {result.mrr:.3f} (should be 1.0 - first doc is relevant)")

    assert result.recall_at_5 == 1.0, "Perfect retrieval should have Recall@5 = 1.0"
    assert result.ndcg_at_5 == 1.0, "Perfect ranking should have nDCG@5 = 1.0"
    assert result.mrr == 1.0, "First doc relevant should give MRR = 1.0"

    print("✅ Perfect retrieval test passed!\n")


@pytest.mark.unit
def test_poor_retrieval():
    """Test case where retrieval is poor."""

    print("\n\nTEST: Poor Retrieval")
    print("=" * 60)

    retrieved_docs = [
        _doc(doc_id="doc1", text="Not relevant", score=1.0),
        _doc(doc_id="doc2", text="Also not relevant", score=0.9),
        _doc(doc_id="doc3", text="Still not relevant", score=0.8),
    ]

    reranker_judgments = [
        _judgment(doc_id="doc1", relevance_score=0.1),
        _judgment(doc_id="doc2", relevance_score=0.2),
        _judgment(doc_id="doc3", relevance_score=0.15),
    ]

    metrics = RetrievalMetrics(relevance_threshold=0.5)
    result = metrics.compute_all_metrics(
        retrieved_docs=retrieved_docs,
        reranker_judgments=reranker_judgments,
        k_values=[5, 10, 15],
    )

    print(f"Recall@5:  {result.recall_at_5:.3f} (should be 0.0 - no relevant docs)")
    print(f"MRR:       {result.mrr:.3f} (should be 0.0 - no relevant docs found)")
    print(f"Hit@5:     {result.hit_rate_at_5:.3f} (should be 0.0)")

    assert result.recall_at_5 == 0.0, "No relevant docs should give Recall@5 = 0.0"
    assert result.mrr == 0.0, "No relevant docs should give MRR = 0.0"
    assert result.hit_rate_at_5 == 0.0, "No relevant docs should give Hit@5 = 0.0"

    print("✅ Poor retrieval test passed!\n")


@pytest.mark.unit
def test_metrics_with_empty_results():
    """Test edge case with empty results."""
    metrics = RetrievalMetrics(relevance_threshold=0.5)

    result = metrics.compute_all_metrics(
        retrieved_docs=[],
        reranker_judgments=[],
        k_values=[5, 10, 15],
    )

    assert result.recall_at_5 == 0.0
    assert result.ndcg_at_5 == 0.0
    assert result.mrr == 0.0
    assert result.num_relevant_docs == 0
    assert result.num_retrieved_docs == 0


@pytest.mark.unit
def test_metrics_with_partial_relevant():
    """Test case with some relevant, some not relevant."""
    retrieved_docs = [
        _doc(doc_id="doc1", text="Relevant", score=0.9),
        _doc(doc_id="doc2", text="Not relevant", score=0.8),
        _doc(doc_id="doc3", text="Relevant", score=0.7),
        _doc(doc_id="doc4", text="Not relevant", score=0.6),
        _doc(doc_id="doc5", text="Relevant", score=0.5),
    ]

    reranker_judgments = [
        _judgment(doc_id="doc1", relevance_score=0.9),
        _judgment(doc_id="doc2", relevance_score=0.2),
        _judgment(doc_id="doc3", relevance_score=0.8),
        _judgment(doc_id="doc4", relevance_score=0.1),
        _judgment(doc_id="doc5", relevance_score=0.7),
    ]

    metrics = RetrievalMetrics(relevance_threshold=0.5)
    result = metrics.compute_all_metrics(
        retrieved_docs=retrieved_docs,
        reranker_judgments=reranker_judgments,
        k_values=[5, 10, 15],
    )

    assert result.num_relevant_docs == 3
    assert result.recall_at_5 == 1.0
    assert result.hit_rate_at_5 == 1.0
    assert 0 < result.ndcg_at_5 <= 1.0


if __name__ == "__main__":
    # Allow running directly with python for quick tests
    test_basic_metrics()
    test_perfect_retrieval()
    test_poor_retrieval()
    test_metrics_with_empty_results()
    test_metrics_with_partial_relevant()
    print("\n🎉 All tests passed! Retrieval metrics are working correctly.\n")
