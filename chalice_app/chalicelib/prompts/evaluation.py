"""
Evaluation prompts for LLM-as-judge quality assessment.

These prompts are used to evaluate:
1. Faithfulness: Whether responses are grounded in provided context
2. Actionability: How actionable and specific recommendations are
3. Retrieval Relevance: How relevant retrieved documents are to the query

Each constant is the baked-in fallback. Prefer the matching `get_*()`
function so the active version comes from LangSmith Hub and the
@traceable run records which prompt version produced it.
"""

from typing import Tuple

from chalicelib.prompts.loader import get_prompt

FAITHFULNESS_SYSTEM_PROMPT_HUB = "shopping-assistant-eval-faithfulness-system"
FAITHFULNESS_USER_PROMPT_HUB = "shopping-assistant-eval-faithfulness-user"
ACTIONABILITY_SYSTEM_PROMPT_HUB = "shopping-assistant-eval-actionability-system"
ACTIONABILITY_USER_PROMPT_HUB = "shopping-assistant-eval-actionability-user"
RETRIEVAL_RELEVANCE_SYSTEM_PROMPT_HUB = (
    "shopping-assistant-eval-retrieval-relevance-system"
)
RETRIEVAL_RELEVANCE_USER_PROMPT_HUB = "shopping-assistant-eval-retrieval-relevance-user"


def _escape_braces(s: str) -> str:
    """Hub-stored eval system prompts have JSON-example braces escaped so
    `str.format` renders the example as literal JSON. The fallback uses the
    same escape so behavior is identical whether Hub or fallback wins."""
    return s.replace("{", "{{").replace("}", "}}")


# --- Faithfulness Evaluation ---

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

# --- Actionability Evaluation ---

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

# --- Retrieval Relevance Evaluation ---

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


def get_faithfulness_system() -> Tuple[str, str]:
    """No placeholders. Call `.format()` to unescape JSON braces."""
    return get_prompt(
        FAITHFULNESS_SYSTEM_PROMPT_HUB,
        fallback=_escape_braces(FAITHFULNESS_SYSTEM_PROMPT),
    )


def get_faithfulness_user() -> Tuple[str, str]:
    """Call `.format(query=..., context=..., response=...)`."""
    return get_prompt(FAITHFULNESS_USER_PROMPT_HUB, fallback=FAITHFULNESS_USER_PROMPT)


def get_actionability_system() -> Tuple[str, str]:
    """No placeholders. Call `.format()` to unescape JSON braces."""
    return get_prompt(
        ACTIONABILITY_SYSTEM_PROMPT_HUB,
        fallback=_escape_braces(ACTIONABILITY_SYSTEM_PROMPT),
    )


def get_actionability_user() -> Tuple[str, str]:
    """Call `.format(query=..., response=...)`."""
    return get_prompt(ACTIONABILITY_USER_PROMPT_HUB, fallback=ACTIONABILITY_USER_PROMPT)


def get_retrieval_relevance_system() -> Tuple[str, str]:
    """No placeholders. Call `.format()` to unescape JSON braces."""
    return get_prompt(
        RETRIEVAL_RELEVANCE_SYSTEM_PROMPT_HUB,
        fallback=_escape_braces(RETRIEVAL_RELEVANCE_SYSTEM_PROMPT),
    )


def get_retrieval_relevance_user() -> Tuple[str, str]:
    """Call `.format(query=..., docs=...)`."""
    return get_prompt(
        RETRIEVAL_RELEVANCE_USER_PROMPT_HUB,
        fallback=RETRIEVAL_RELEVANCE_USER_PROMPT,
    )
