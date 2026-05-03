"""Generate and persist starter prompts for the empty-state UI.

The cron passes a list of indexed subreddit names to the LLM, asks for a
grounded pool of starter prompts, validates the JSON response, and overwrites
the single global SuggestedPrompts record. Stale prompts are kept on
validation failure so the homepage never goes blank when a regen run hiccups.

The grounding signal is intentionally lightweight: just the names of the
communities the assistant has indexed. Adding post titles or vector samples
would give richer signal but the names alone already pin the LLM to product
categories the system can actually answer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from chalicelib.aws.dynamo.tables import SuggestedPrompts
from chalicelib.llm import LLMFactory, LLMProvider
from chalicelib.models.data_objects import ChatMessage
from chalicelib.prompts import (
    SUGGESTED_PROMPTS_SYSTEM_PROMPT,
    SUGGESTED_PROMPTS_USER_PROMPT,
)

logger = logging.getLogger(__name__)


TARGET_PROMPT_COUNT = 24
MIN_ACCEPTABLE_PROMPTS = 16  # below this we keep the previous record


# Subreddits the indexer has data for. Update this when you add or remove
# communities from the scrape pipeline. Discovered initially from the S3 raw
# zone partition layout (s3://shopping-assistant-raw-reddit-data/top_posts/).
INDEXED_SUBREDDITS: List[str] = [
    "SkincareAddiction",
    "android",
    "apple",
    "askhistorians",
    "backpacks",
    "beauty",
    "buildapcsales",
    "buyitforlife",
    "coffee",
    "cooking",
    "deals",
    "diy",
    "edc",
    "femalefashionadvice",
    "frugal",
    "frugalliving",
    "frugalmalefashion",
    "gadgets",
    "gamedeals",
    "gaming",
    "gamingdeals",
    "goodvalue",
    "headphones",
    "homeautomation",
    "homeimprovement",
    "interiordesign",
    "ios",
    "malefashionadvice",
    "mealprepsunday",
    "minimalism",
    "personalfinance",
    "productivity",
    "rawdenim",
    "shutupandtakemymoney",
    "smarthome",
    "suggestalaptop",
    "techsupport",
    "thriftstorehauls",
    "travelhacks",
    "whatisthisthing",
    "zerowaste",
]


@dataclass
class GroundingSignal:
    """List of indexed subreddit names used to seed the LLM prompt."""

    subreddits: List[str]

    @property
    def is_empty(self) -> bool:
        return not self.subreddits


def collect_grounding_signal(
    subreddits: Optional[Sequence[str]] = None,
) -> GroundingSignal:
    """Build the grounding snapshot.

    Defaults to the curated INDEXED_SUBREDDITS list; tests can pass an
    arbitrary list in.
    """
    source = list(subreddits) if subreddits is not None else INDEXED_SUBREDDITS
    cleaned = [s.strip() for s in source if s and s.strip()]
    return GroundingSignal(subreddits=cleaned)


def build_llm_prompt(
    signal: GroundingSignal, *, target_count: int = TARGET_PROMPT_COUNT
) -> str:
    """Fill the SUGGESTED_PROMPTS_USER_PROMPT template with the subreddit list."""
    subreddit_lines = (
        "\n".join(f"- r/{name}" for name in signal.subreddits)
        or "- (no community signal available)"
    )
    return SUGGESTED_PROMPTS_USER_PROMPT.format(
        target_count=target_count,
        subreddit_lines=subreddit_lines,
    )


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_llm_response(raw: str) -> List[str]:
    """Parse the LLM output into a clean list of prompt strings.

    Tolerates: bare JSON object, JSON inside ```json fenced blocks, JSON with
    leading/trailing prose. Raises ValueError if no usable JSON is found.
    """
    if not raw:
        raise ValueError("Empty LLM response")

    candidates: List[str] = []
    fenced = _JSON_BLOCK_RE.search(raw)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(raw.strip())

    # Last-ditch: slice between first { and last }.
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(raw[first : last + 1])

    parsed = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    if parsed is None:
        raise ValueError("Could not parse JSON from LLM response")

    prompts = parsed.get("prompts") if isinstance(parsed, dict) else None
    if not isinstance(prompts, list):
        raise ValueError("LLM response missing 'prompts' list")

    cleaned: List[str] = []
    seen = set()
    for entry in prompts:
        if not isinstance(entry, str):
            continue
        text = entry.strip().strip('"').strip()
        if not text:
            continue
        word_count = len(text.split())
        if word_count < 4 or word_count > 18:
            continue
        # Dedupe case-insensitively to avoid near-duplicates dominating the pool.
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)

    return cleaned


def regenerate_prompts(
    *,
    subreddits: Optional[Sequence[str]] = None,
    llm_provider: LLMProvider = LLMProvider.DEEPSEEK,
) -> SuggestedPrompts:
    """Run the full grounding → LLM → parse → persist pipeline.

    Raises if the grounding signal is empty or the regenerated set is too
    small to publish; callers should let the cron fail loudly so SNS alarms
    fire — leaving the previous record in place keeps the homepage populated
    until the next run.
    """
    signal = collect_grounding_signal(subreddits=subreddits)
    if signal.is_empty:
        raise RuntimeError(
            "No grounding signal available; aborting prompt regeneration"
        )

    user_prompt = build_llm_prompt(signal)
    llm = LLMFactory.create_llm(provider=llm_provider)
    response_text = llm.chat(
        messages=[
            ChatMessage(role="system", content=SUGGESTED_PROMPTS_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_prompt),
        ]
    )
    logger.info(
        "Got LLM response for suggested-prompts regeneration",
        extra={"length": len(response_text or "")},
    )

    prompts = parse_llm_response(response_text or "")
    if len(prompts) < MIN_ACCEPTABLE_PROMPTS:
        raise ValueError(
            f"Validated only {len(prompts)} prompts; need at least "
            f"{MIN_ACCEPTABLE_PROMPTS}"
        )

    record = SuggestedPrompts.new(
        prompts=prompts[:TARGET_PROMPT_COUNT],
        sources_used=signal.subreddits,
    )
    record.save()
    logger.info(
        "Persisted SuggestedPrompts",
        extra={
            "count": len(record.prompts),
            "source_count": len(record.sources_used),
        },
    )
    return record


def load_or_default() -> List[str]:
    """Read cached prompts; return a small static fallback if missing."""
    record = SuggestedPrompts.load()
    if record and record.prompts:
        return record.prompts
    return _STATIC_FALLBACK


# Used only when the cache is empty (e.g., before the very first cron run).
_STATIC_FALLBACK: List[str] = [
    "Find me budget noise-cancelling headphones under $150",
    "Best gift under $50 for a weekend hiker",
    "Recommend a quality starter chef's knife",
    "Hiking boots that handle rain and mud",
]
