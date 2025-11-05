"""LLM-based reranker for search results."""

import json
from typing import Dict, List

from langsmith import traceable

from chalicelib.core.logger_config import setup_logger
from chalicelib.llm import LLMFactory, LLMProvider
from chalicelib.models.data_objects import ChatMessage, RerankerJudgment

logger = setup_logger(__name__)


class LLMReranker:
    def __init__(self, llm_provider: LLMProvider = LLMProvider.DEEPSEEK):
        self.llm = LLMFactory.create_llm(provider=llm_provider)
        self.last_relevance_scores: List[RerankerJudgment] = (
            []
        )  # Store relevance scores from last rerank

    @traceable(name="llm_rerank")
    def rerank(self, query: str, results: List[Dict], limit: int) -> List[Dict]:
        if not results or len(results) <= limit:
            return results[:limit]

        try:
            # Prepare results for LLM evaluation
            results_text = self._prepare_results_for_llm(results)

            # Create prompt for LLM reranking with relevance scores
            rerank_prompt = self._create_rerank_prompt(query, results_text)

            # Get LLM ranking with JSON mode enabled for clean JSON output
            ranking_response = self.llm.chat(
                [ChatMessage(role="user", content=rerank_prompt)], json_mode=True
            )

            # Parse and apply the ranking, also extract relevance scores
            return self._parse_and_apply_ranking(ranking_response, results, limit)

        except Exception as e:
            logger.error(f"Error during LLM reranking: {e}. Using original ranking.")
            return results[:limit]

    def get_relevance_scores(self) -> List[RerankerJudgment]:
        """
        Get relevance scores from the last reranking operation.

        Returns:
            List of RerankerJudgment dataclasses with doc_id and relevance_score
        """
        return self.last_relevance_scores

    def _prepare_results_for_llm(self, results: List[Dict]) -> str:
        results_text = []
        for i, result in enumerate(results):
            text = result["payload"]["text"][:500]  # Truncate for LLM efficiency
            results_text.append(f"{i+1}. {text}")

        return "\n\n".join(results_text)

    def _create_rerank_prompt(self, query: str, results_text: str) -> str:

        return f"""You are a search relevance expert. Given a user query and a list of search results, rank them by relevance and assign relevance scores.

User Query: "{query}"

Search Results:
{results_text}

Please evaluate each result and provide:
1. A ranking from most to least relevant
2. A relevance score (0.0 to 1.0) for each result where:
   - 1.0 = Highly relevant, directly answers the query
   - 0.7-0.9 = Relevant, contains useful information
   - 0.4-0.6 = Somewhat relevant, tangentially related
   - 0.0-0.3 = Not relevant

Return a JSON object:

{{
  "ranking": [3, 1, 5, 2, 4],
  "scores": [0.95, 0.85, 0.70, 0.60, 0.30]
}}

Where "ranking" shows the order (most to least relevant) and "scores" contains the relevance score for each result in the same order as the ranking. Return ONLY the JSON object, no additional text."""

    def _parse_and_apply_ranking(
        self, ranking_response: str, results: List[Dict], limit: int
    ) -> List[Dict]:
        """Parse LLM ranking response and apply it to results, extracting relevance scores."""
        try:
            # Parse JSON response
            ranking_data = json.loads(ranking_response.strip())
            ranking_numbers = [
                int(x) - 1 for x in ranking_data["ranking"]
            ]  # Convert to 0-based index

            # Extract relevance scores if available
            relevance_scores = ranking_data.get("scores", [])

            # Store relevance scores for retrieval metrics
            # Format: [{"doc_id": text_hash, "relevance_score": score}, ...]
            self.last_relevance_scores = []

            if relevance_scores and len(relevance_scores) == len(ranking_numbers):
                for i, rank_idx in enumerate(ranking_numbers):
                    if 0 <= rank_idx < len(results):
                        doc_text = results[rank_idx].get("payload", {}).get("text", "")
                        doc_id = self._get_doc_id(doc_text)
                        relevance_score = relevance_scores[i]
                        judgment = RerankerJudgment(
                            doc_id=doc_id, relevance_score=float(relevance_score)
                        )
                        self.last_relevance_scores.append(judgment)

            # If we got a valid ranking, reorder results
            if ranking_numbers and len(ranking_numbers) >= min(len(results), limit):
                reranked_results = []
                for rank_idx in ranking_numbers[:limit]:
                    if 0 <= rank_idx < len(results):
                        reranked_results.append(results[rank_idx])

                logger.info(
                    f"LLM reranked {len(results)} results to top {len(reranked_results)} with relevance scores"
                )
                return reranked_results

        except (ValueError, IndexError, KeyError, json.JSONDecodeError) as e:
            logger.warning(
                f"Failed to parse LLM ranking JSON response: {e}. Response was: {ranking_response}. Using original ranking."
            )

        # Fallback to original ranking if LLM ranking fails
        return results[:limit]

    def _get_doc_id(self, doc_text: str) -> str:
        """
        Generate a consistent document ID from text content.
        Uses hash of text to create stable identifier.
        """
        import hashlib

        # MD5 used for non-cryptographic document ID generation only
        return hashlib.md5(doc_text.encode(), usedforsecurity=False).hexdigest()
