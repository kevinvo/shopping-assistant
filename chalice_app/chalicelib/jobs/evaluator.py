"""Evaluation job workers for post-response quality checks."""

import json
import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from langsmith import Client

from chalicelib.core.config import AppConfig
from chalicelib.llm import LLMFactory, LLMProvider
from chalicelib.models.data_objects import ChatMessage, EvaluationMessage
from chalicelib.llm.metrics import RetrievalMetrics, RetrievalMetricsResult


logger = logging.getLogger()
logger.setLevel(logging.INFO)


@dataclass
class FeedbackEntry:
    """Represents a single feedback entry to post to LangSmith."""

    key: str
    score: float
    comment: str


@dataclass
class EvaluationScores:
    """Represents the evaluation scores for a single request."""

    overall_score: float
    evaluation_tier: str
    heuristic_score: float
    has_products: bool
    has_specifics: bool
    response_length: int

    faithfulness: Optional[float] = None
    faithfulness_reasoning: Optional[str] = None
    actionability_llm: Optional[float] = None
    actionability_reasoning: Optional[str] = None
    retrieval_relevance: Optional[float] = None
    retrieval_reasoning: Optional[str] = None

    recall_at_5: Optional[float] = None
    recall_at_10: Optional[float] = None
    recall_at_15: Optional[float] = None
    ndcg_at_5: Optional[float] = None
    ndcg_at_10: Optional[float] = None
    ndcg_at_15: Optional[float] = None
    mrr: Optional[float] = None
    hit_rate_at_5: Optional[float] = None
    hit_rate_at_10: Optional[float] = None
    hit_rate_at_15: Optional[float] = None


@dataclass
class FaithfulnessResult:
    """Result from faithfulness evaluation."""

    faithfulness: float
    grounded: bool
    reasoning: str


@dataclass
class ActionabilityResult:
    """Result from actionability evaluation."""

    actionability: float
    specific_products_count: int
    reasoning: str


@dataclass
class RetrievalRelevanceResult:
    """Result from retrieval relevance evaluation."""

    avg_relevance: float
    reasoning: str


@dataclass
class HeuristicResult:
    """Result from heuristic checks."""

    heuristic_score: float
    has_products: bool
    has_specifics: bool
    response_length: int


@dataclass
class ScoresForComputation:
    """Scores used for computing overall score."""

    heuristic_score: float
    faithfulness: Optional[float] = None
    actionability_llm: Optional[float] = None
    retrieval_relevance: Optional[float] = None


config = AppConfig()
langsmith_client = Client(
    api_key=config.langsmith_api_key,
    api_url=config.langsmith_api_url,
)

judge_llm = LLMFactory.create_llm(provider=LLMProvider.DEEPSEEK)
logger.info("Initialized DeepSeek judge LLM using LLMFactory")


FAITHFULNESS_SYSTEM_PROMPT = """Evaluate if the assistant's response is grounded in the provided Reddit context.
Check if specific claims, products, or recommendations in the response can be traced back to the context.

Score 0-1:
- 1.0 = All claims are grounded in context, no hallucinations
- 0.7 = Mostly grounded, minor unverifiable details
- 0.4 = Some grounded, some made-up information
- 0.0 = Response ignores context or makes up information

Respond with ONLY a JSON object:
{"faithfulness": 0.9, "grounded": true, "reasoning": "brief explanation"}"""

FAITHFULNESS_USER_PROMPT = """User Query: {query}

Reddit Context Provided:
{context}

Assistant Response:
{response}

Evaluate faithfulness:"""

ACTIONABILITY_SYSTEM_PROMPT = """Rate how actionable this shopping recommendation is.

First, assess if the user query has enough information to provide specific recommendations.
- If the query lacks context (no budget, use case, preferences), asking clarifying questions is APPROPRIATE and should score high (0.8-1.0).
- If the query has enough context, rate the specificity of product recommendations.

Consider:
- Specific product names mentioned
- Clear pros/cons or comparisons
- Concrete next steps for the user
- Price/value information
- Appropriate clarifying questions when context is missing

Score 0-1:
- 1.0 = Highly actionable with specific recommendations OR asks relevant clarifying questions for vague queries
- 0.7 = Good recommendations but could be more specific
- 0.4 = Generic advice without specific products when specifics were possible
- 0.0 = Vague/unhelpful or provides generic recommendations when query needed clarification

Respond with ONLY a JSON object:
{"actionability": 0.9, "specific_products_count": 3, "reasoning": "brief explanation"}"""

ACTIONABILITY_USER_PROMPT = """User Query: {query}

Assistant Response:
{response}

Evaluate actionability:"""

RETRIEVAL_RELEVANCE_SYSTEM_PROMPT = """Rate the relevance of retrieved Reddit documents to the user's shopping query.

Score 0-1 where:
- 1.0 = Highly relevant, directly addresses the shopping query
- 0.7 = Relevant, contains useful product information
- 0.4 = Somewhat relevant, tangential information
- 0.0 = Not relevant at all

Respond with ONLY a JSON object:
{"avg_relevance": 0.8, "reasoning": "brief explanation"}"""

RETRIEVAL_RELEVANCE_USER_PROMPT = """User Query: {query}

Retrieved Reddit Documents:
{docs}

Evaluate relevance:"""


def process_evaluation_task(eval_message: EvaluationMessage) -> None:
    """Process a single evaluation task."""

    query = eval_message.query
    response = eval_message.response
    request_id = eval_message.request_id
    metadata = eval_message.metadata

    logger.info("Evaluating request %s", request_id)

    scores = run_comprehensive_evaluation(
        query=query, response=response, request_id=request_id, metadata=metadata
    )

    run_id = metadata.get("run_id")

    if not run_id:
        logger.warning(
            "No run_id provided for request %s, skipping feedback", request_id
        )
        return

    try:
        logger.info("Posting feedback to run_id: %s", run_id)

        feedbacks_to_post: List[FeedbackEntry] = [
            FeedbackEntry(
                key="overall_score",
                score=scores.overall_score,
                comment=(
                    "Weighted average of all evaluation metrics (faithfulness 40%, "
                    "actionability 35%, retrieval 25%). Tier: "
                    f"{scores.evaluation_tier}"
                ),
            ),
            FeedbackEntry(
                key="heuristic_score",
                score=scores.heuristic_score,
                comment=(
                    "Fast heuristic checks for product mentions, brand names, and "
                    "response length. Details: "
                    + json.dumps(
                        {
                            "has_products": scores.has_products,
                            "has_specifics": scores.has_specifics,
                            "response_length": scores.response_length,
                        }
                    )
                ),
            ),
        ]

        if scores.faithfulness is not None:
            feedbacks_to_post.extend(
                [
                    FeedbackEntry(
                        key="faithfulness",
                        score=scores.faithfulness,
                        comment=(
                            "How well the response is grounded in provided Reddit "
                            "context (1.0 = fully grounded, 0.0 = hallucinations)."
                        ),
                    ),
                    FeedbackEntry(
                        key="faithfulness_reasoning",
                        score=scores.faithfulness,
                        comment=scores.faithfulness_reasoning or "N/A",
                    ),
                ]
            )

        if scores.actionability_llm is not None:
            feedbacks_to_post.extend(
                [
                    FeedbackEntry(
                        key="actionability",
                        score=scores.actionability_llm,
                        comment=(
                            "How actionable and specific the product recommendations are"
                            " (1.0 = highly actionable, 0.0 = vague)."
                        ),
                    ),
                    FeedbackEntry(
                        key="actionability_reasoning",
                        score=scores.actionability_llm,
                        comment=scores.actionability_reasoning or "N/A",
                    ),
                ]
            )

        if scores.retrieval_relevance is not None:
            feedbacks_to_post.extend(
                [
                    FeedbackEntry(
                        key="retrieval_relevance",
                        score=scores.retrieval_relevance,
                        comment=(
                            "How relevant the retrieved Reddit documents are to the "
                            "user's query (1.0 = highly relevant, 0.0 = not relevant)."
                        ),
                    ),
                    FeedbackEntry(
                        key="retrieval_relevance_reasoning",
                        score=scores.retrieval_relevance,
                        comment=scores.retrieval_reasoning or "N/A",
                    ),
                ]
            )

        if (
            scores.recall_at_5 is not None
            and scores.recall_at_10 is not None
            and scores.recall_at_15 is not None
            and scores.ndcg_at_5 is not None
            and scores.ndcg_at_10 is not None
            and scores.ndcg_at_15 is not None
            and scores.mrr is not None
            and scores.hit_rate_at_5 is not None
            and scores.hit_rate_at_10 is not None
            and scores.hit_rate_at_15 is not None
        ):
            feedbacks_to_post.extend(
                [
                    FeedbackEntry(
                        key="recall_at_5",
                        score=scores.recall_at_5,
                        comment="Proportion of relevant docs found in top 5 results",
                    ),
                    FeedbackEntry(
                        key="recall_at_10",
                        score=scores.recall_at_10,
                        comment="Proportion of relevant docs found in top 10 results",
                    ),
                    FeedbackEntry(
                        key="recall_at_15",
                        score=scores.recall_at_15,
                        comment="Proportion of relevant docs found in top 15 results",
                    ),
                    FeedbackEntry(
                        key="ndcg_at_5",
                        score=scores.ndcg_at_5,
                        comment="Normalized Discounted Cumulative Gain at 5",
                    ),
                    FeedbackEntry(
                        key="ndcg_at_10",
                        score=scores.ndcg_at_10,
                        comment="Normalized Discounted Cumulative Gain at 10",
                    ),
                    FeedbackEntry(
                        key="ndcg_at_15",
                        score=scores.ndcg_at_15,
                        comment="Normalized Discounted Cumulative Gain at 15",
                    ),
                    FeedbackEntry(
                        key="mrr",
                        score=scores.mrr,
                        comment="Mean Reciprocal Rank",
                    ),
                    FeedbackEntry(
                        key="hit_rate_at_5",
                        score=scores.hit_rate_at_5,
                        comment="Whether any relevant doc appears in top 5",
                    ),
                    FeedbackEntry(
                        key="hit_rate_at_10",
                        score=scores.hit_rate_at_10,
                        comment="Whether any relevant doc appears in top 10",
                    ),
                    FeedbackEntry(
                        key="hit_rate_at_15",
                        score=scores.hit_rate_at_15,
                        comment="Whether any relevant doc appears in top 15",
                    ),
                ]
            )

        posted_count = 0
        for feedback in feedbacks_to_post:
            try:
                langsmith_client.create_feedback(
                    run_id=run_id,
                    key=feedback.key,
                    score=feedback.score,
                    comment=feedback.comment,
                )
                posted_count += 1
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Failed to post %s feedback: %s", feedback.key, exc)

        logger.info(
            "Posted %s/%s feedbacks for run %s, overall: %.3f",
            posted_count,
            len(feedbacks_to_post),
            run_id,
            scores.overall_score,
        )
    except Exception as ls_error:  # pragma: no cover - defensive logging
        logger.error("LangSmith feedback error for run %s: %s", run_id, ls_error)


def run_comprehensive_evaluation(
    query: str, response: str, request_id: str, metadata: dict
) -> EvaluationScores:
    """Run tiered evaluations based on sampling."""

    retrieved_docs = metadata.get("top_results", [])
    context = metadata.get("search_context", "")

    heuristic_scores = run_heuristic_checks(response)

    evaluation_tier = "full_llm"
    faithfulness = None
    faithfulness_reasoning = None
    actionability_llm = None
    actionability_reasoning = None
    retrieval_relevance = None
    retrieval_reasoning = None

    retrieval_metrics_result = None

    try:
        logger.info("Running full LLM evaluation for request %s", request_id)

        faithfulness_result = evaluate_faithfulness(query, context, response)
        faithfulness = faithfulness_result.faithfulness
        faithfulness_reasoning = faithfulness_result.reasoning

        actionability_result = evaluate_actionability_llm(query, response)
        actionability_llm = actionability_result.actionability
        actionability_reasoning = actionability_result.reasoning

        if retrieved_docs:
            retrieval_result = evaluate_retrieval_relevance(query, retrieved_docs)
            retrieval_relevance = retrieval_result.avg_relevance
            retrieval_reasoning = retrieval_result.reasoning

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("LLM evaluation failed: %s", exc, exc_info=True)

    try:
        pre_rerank_results = metadata.get("pre_rerank_results", [])
        reranker_scores = metadata.get("reranker_scores", [])

        if pre_rerank_results and reranker_scores:
            logger.info("Computing retrieval metrics for request %s", request_id)
            retrieval_metrics_result = compute_retrieval_metrics(
                pre_rerank_results, reranker_scores
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Retrieval metrics computation failed: %s", exc, exc_info=True)

    scores_for_computation = ScoresForComputation(
        heuristic_score=heuristic_scores.heuristic_score,
        faithfulness=faithfulness,
        actionability_llm=actionability_llm,
        retrieval_relevance=retrieval_relevance,
    )

    overall_score = compute_overall_score(scores_for_computation)

    return EvaluationScores(
        overall_score=overall_score,
        evaluation_tier=evaluation_tier,
        heuristic_score=heuristic_scores.heuristic_score,
        has_products=heuristic_scores.has_products,
        has_specifics=heuristic_scores.has_specifics,
        response_length=heuristic_scores.response_length,
        faithfulness=faithfulness,
        faithfulness_reasoning=faithfulness_reasoning,
        actionability_llm=actionability_llm,
        actionability_reasoning=actionability_reasoning,
        retrieval_relevance=retrieval_relevance,
        retrieval_reasoning=retrieval_reasoning,
        recall_at_5=(
            retrieval_metrics_result.recall_at_5 if retrieval_metrics_result else None
        ),
        recall_at_10=(
            retrieval_metrics_result.recall_at_10 if retrieval_metrics_result else None
        ),
        recall_at_15=(
            retrieval_metrics_result.recall_at_15 if retrieval_metrics_result else None
        ),
        ndcg_at_5=(
            retrieval_metrics_result.ndcg_at_5 if retrieval_metrics_result else None
        ),
        ndcg_at_10=(
            retrieval_metrics_result.ndcg_at_10 if retrieval_metrics_result else None
        ),
        ndcg_at_15=(
            retrieval_metrics_result.ndcg_at_15 if retrieval_metrics_result else None
        ),
        mrr=retrieval_metrics_result.mrr if retrieval_metrics_result else None,
        hit_rate_at_5=(
            retrieval_metrics_result.hit_rate_at_5 if retrieval_metrics_result else None
        ),
        hit_rate_at_10=(
            retrieval_metrics_result.hit_rate_at_10
            if retrieval_metrics_result
            else None
        ),
        hit_rate_at_15=(
            retrieval_metrics_result.hit_rate_at_15
            if retrieval_metrics_result
            else None
        ),
    )


def run_heuristic_checks(response: str) -> HeuristicResult:
    """Fast heuristic checks (no LLM cost)."""

    has_products = any(
        word in response.lower()
        for word in ["product", "brand", "$", "price", "buy", "purchase"]
    )

    words = response.split()
    capitalized_count = sum(1 for word in words if len(word) > 3 and word[0].isupper())
    has_specifics = capitalized_count >= 2

    response_length = len(words)

    heuristic_score = 0.5
    if has_products:
        heuristic_score += 0.15
    if has_specifics:
        heuristic_score += 0.15
    if response_length > 50:
        heuristic_score += 0.1
    if response_length > 100:
        heuristic_score += 0.1

    return HeuristicResult(
        heuristic_score=min(heuristic_score, 1.0),
        has_products=has_products,
        has_specifics=has_specifics,
        response_length=response_length,
    )


def evaluate_faithfulness(
    query: str, context: str, response: str
) -> FaithfulnessResult:
    """Core Metric #1: Check if response is grounded in provided context."""

    context_preview = context[:2000] if len(context) > 2000 else context

    messages = [
        ChatMessage(role="system", content=FAITHFULNESS_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=FAITHFULNESS_USER_PROMPT.format(
                query=query,
                context=context_preview,
                response=response,
            ),
        ),
    ]

    result = judge_llm.chat(
        messages=messages, temperature=0.0, max_tokens=400, json_mode=True
    )

    try:
        parsed = json.loads(result)
        return FaithfulnessResult(
            faithfulness=parsed.get("faithfulness", 0.5),
            grounded=parsed.get("grounded", False),
            reasoning=parsed.get("reasoning", ""),
        )
    except json.JSONDecodeError:  # pragma: no cover - defensive logging
        logger.warning("Failed to parse faithfulness response: %s", result)
        return FaithfulnessResult(
            faithfulness=0.5, grounded=False, reasoning="Parse failed"
        )


def evaluate_actionability_llm(query: str, response: str) -> ActionabilityResult:
    """Core Metric #2: Rate how actionable and specific the recommendations are."""

    messages = [
        ChatMessage(role="system", content=ACTIONABILITY_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=ACTIONABILITY_USER_PROMPT.format(query=query, response=response),
        ),
    ]

    result = judge_llm.chat(
        messages=messages, temperature=0.0, max_tokens=300, json_mode=True
    )

    try:
        parsed = json.loads(result)
        return ActionabilityResult(
            actionability=parsed.get("actionability", 0.5),
            specific_products_count=parsed.get("specific_products_count", 0),
            reasoning=parsed.get("reasoning", ""),
        )
    except json.JSONDecodeError:  # pragma: no cover - defensive logging
        logger.warning("Failed to parse actionability response: %s", result)
        return ActionabilityResult(
            actionability=0.5, specific_products_count=0, reasoning="Parse failed"
        )


def evaluate_retrieval_relevance(
    query: str, retrieved_docs: List[str]
) -> RetrievalRelevanceResult:
    """Core Metric #3: Judge if retrieved documents are relevant to the query."""

    top_docs = retrieved_docs[:3]
    docs_text = "\n\n".join(
        [
            (
                f"Document {i+1}: {doc[:300]}..."
                if len(doc) > 300
                else f"Document {i+1}: {doc}"
            )
            for i, doc in enumerate(top_docs)
        ]
    )

    messages = [
        ChatMessage(role="system", content=RETRIEVAL_RELEVANCE_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=RETRIEVAL_RELEVANCE_USER_PROMPT.format(query=query, docs=docs_text),
        ),
    ]

    result = judge_llm.chat(
        messages=messages, temperature=0.0, max_tokens=300, json_mode=True
    )

    try:
        parsed = json.loads(result)
        return RetrievalRelevanceResult(
            avg_relevance=parsed.get("avg_relevance", 0.5),
            reasoning=parsed.get("reasoning", ""),
        )
    except json.JSONDecodeError:  # pragma: no cover - defensive logging
        logger.warning("Failed to parse retrieval response: %s", result)
        return RetrievalRelevanceResult(avg_relevance=0.5, reasoning="Parse failed")


def compute_retrieval_metrics(
    pre_rerank_results: List[Any],
    reranker_scores: List[Any],
) -> RetrievalMetricsResult:
    """Compute retrieval metrics using reranker scores as ground truth."""

    if pre_rerank_results and hasattr(pre_rerank_results[0], "to_dict"):
        pre_rerank_dicts = [doc.to_dict() for doc in pre_rerank_results]
    else:
        pre_rerank_dicts = pre_rerank_results

    if reranker_scores and hasattr(reranker_scores[0], "to_dict"):
        reranker_dicts = [score.to_dict() for score in reranker_scores]
    else:
        reranker_dicts = reranker_scores

    metrics_calculator = RetrievalMetrics(relevance_threshold=0.5)

    return metrics_calculator.compute_all_metrics(
        retrieved_docs=pre_rerank_dicts,
        reranker_judgments=reranker_dicts,
        k_values=[5, 10, 15],
    )


def compute_overall_score(scores: ScoresForComputation) -> float:
    """Compute weighted overall score from available metrics."""

    core_scores = []

    if scores.faithfulness is not None:
        core_scores.append(scores.faithfulness * 0.4)

    if scores.actionability_llm is not None:
        core_scores.append(scores.actionability_llm * 0.35)

    if scores.retrieval_relevance is not None:
        core_scores.append(scores.retrieval_relevance * 0.25)

    if core_scores:
        return sum(core_scores)

    return scores.heuristic_score
