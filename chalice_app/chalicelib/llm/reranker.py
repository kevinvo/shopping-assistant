"""BM25-based reranker for search results."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from langsmith import traceable
from rank_bm25 import BM25Okapi

from chalicelib.core.logger_config import setup_logger
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.models.data_objects import RerankerJudgment

logger = setup_logger(__name__)

MAX_DOCUMENT_TEXT_LENGTH = 4000


@dataclass
class RerankerInput:
    text: str
    metadata: Dict[str, Any]
    score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload": {"text": self.text, "metadata": self.metadata},
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RerankerInput":
        payload = data.get("payload", {})
        return cls(
            text=payload.get("text", ""),
            metadata=payload.get("metadata", {}),
            score=data.get("score", 0.0),
        )


def _tokenize(text: str) -> List[str]:
    text_lower = text.lower()
    tokens = re.findall(r"\b\w+\b", text_lower)
    return tokens


class BM25Reranker:
    def __init__(self):
        self.last_relevance_scores: List[RerankerJudgment] = []

    @traceable(name="bm25_rerank")
    @measure_execution_time
    def rerank(
        self, query: str, results: List[RerankerInput], limit: int
    ) -> List[RerankerInput]:
        if not results or len(results) <= limit:
            return results[:limit]

        try:
            query_tokens = _tokenize(query)

            doc_texts = []
            for result in results:
                doc_text = result.text
                if len(doc_text) > MAX_DOCUMENT_TEXT_LENGTH:
                    doc_text = doc_text[:MAX_DOCUMENT_TEXT_LENGTH]
                doc_texts.append(doc_text)

            tokenized_docs = [_tokenize(doc) for doc in doc_texts]

            if not tokenized_docs or not any(tokenized_docs):
                logger.warning("No valid documents to rerank")
                return results[:limit]

            bm25 = BM25Okapi(tokenized_docs)
            scores = bm25.get_scores(query_tokens)

            min_score = min(scores) if scores else 0.0
            max_score = max(scores) if scores else 1.0
            score_range = max_score - min_score if max_score > min_score else 1.0

            normalized_scores = [
                (score - min_score) / score_range if score_range > 0 else 0.0
                for score in scores
            ]

            scored_results = [
                (i, float(normalized_scores[i]), results[i])
                for i in range(len(results))
            ]
            scored_results.sort(key=lambda x: x[1], reverse=True)

            self.last_relevance_scores = []
            reranked_results = []

            for idx, score, result in scored_results[:limit]:
                doc_id = self._get_doc_id(result.text)
                judgment = RerankerJudgment(doc_id=doc_id, relevance_score=score)
                self.last_relevance_scores.append(judgment)
                reranked_results.append(result)

            logger.info(
                f"BM25 reranked {len(results)} results to top {len(reranked_results)} with relevance scores"
            )
            return reranked_results

        except Exception as e:
            logger.error(f"Error during BM25 reranking: {e}. Using original ranking.")
            return results[:limit]

    def get_relevance_scores(self) -> List[RerankerJudgment]:
        return self.last_relevance_scores

    def _get_doc_id(self, doc_text: str) -> str:
        return hashlib.md5(doc_text.encode(), usedforsecurity=False).hexdigest()
