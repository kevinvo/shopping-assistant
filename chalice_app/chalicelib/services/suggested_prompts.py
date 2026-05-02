"""Generate and persist starter prompts for the empty-state UI.

The cron pulls a grounding signal from the indexed Reddit content (top
subreddits + sampled post titles), feeds it to the LLM along with formatting
constraints, parses the JSON response, validates it, and overwrites the single
global SuggestedPrompts record. Stale prompts are kept on validation failure
so the homepage never goes blank when a regen run hiccups.
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Sequence

import boto3

from chalicelib.aws.dynamo.tables import SuggestedPrompts
from chalicelib.core.config import REDDIT_POSTS_TABLE_NAME
from chalicelib.llm import LLMFactory, LLMProvider
from chalicelib.models.data_objects import ChatMessage

logger = logging.getLogger(__name__)


TOP_SUBREDDITS_K = 20
SAMPLE_TITLES_K = 30
TARGET_PROMPT_COUNT = 24
MIN_ACCEPTABLE_PROMPTS = 16  # below this we keep the previous record
SCAN_PAGE_LIMIT = 500


@dataclass
class GroundingSignal:
    """Snapshot of indexed content used to seed the LLM prompt."""

    subreddit_counts: List[tuple]  # [(name, count), ...] ordered desc
    sample_titles: List[str]

    @property
    def is_empty(self) -> bool:
        return not self.subreddit_counts and not self.sample_titles


def _scan_reddit_posts(
    table_name: str = REDDIT_POSTS_TABLE_NAME,
    page_limit: int = SCAN_PAGE_LIMIT,
    max_pages: int = 4,
) -> List[dict]:
    """Scan reddit-posts DDB table for grounding signal.

    Bounded by max_pages to keep the cron cheap; we don't need every row,
    just enough variety to characterize the catalog.
    """
    table = boto3.resource("dynamodb").Table(table_name)
    items: List[dict] = []
    last_key = None
    for _ in range(max_pages):
        kwargs = {
            "Limit": page_limit,
            "ProjectionExpression": "subreddit, title",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    return items


def collect_grounding_signal(
    items: Optional[Sequence[dict]] = None,
    *,
    top_k: int = TOP_SUBREDDITS_K,
    sample_k: int = SAMPLE_TITLES_K,
    rng: Optional[random.Random] = None,
) -> GroundingSignal:
    """Build the grounding snapshot from raw DDB items.

    Pulled out as a pure function so tests can pass canned items without
    needing DynamoDB.
    """
    rng = rng or random.Random()
    raw = list(items) if items is not None else _scan_reddit_posts()

    subreddit_counter: Counter = Counter()
    titles_by_subreddit: dict = {}
    for item in raw:
        subreddit = (item.get("subreddit") or "").strip()
        title = (item.get("title") or "").strip()
        if not subreddit:
            continue
        subreddit_counter[subreddit] += 1
        if title:
            titles_by_subreddit.setdefault(subreddit, []).append(title)

    top = subreddit_counter.most_common(top_k)
    top_names = {name for name, _ in top}

    # Sample titles distributed across the top subreddits so the LLM sees
    # representative breadth, not all titles from one dominant community.
    eligible_titles: List[str] = []
    for name in top_names:
        eligible_titles.extend(titles_by_subreddit.get(name, []))
    rng.shuffle(eligible_titles)
    sample_titles = eligible_titles[:sample_k]

    return GroundingSignal(subreddit_counts=top, sample_titles=sample_titles)


def build_llm_prompt(
    signal: GroundingSignal, *, target_count: int = TARGET_PROMPT_COUNT
) -> str:
    """Build the user-message prompt for the LLM."""
    subreddit_lines = (
        "\n".join(
            f"- {name} ({count} posts)" for name, count in signal.subreddit_counts
        )
        or "- (no community signal available)"
    )
    title_lines = (
        "\n".join(f'- "{t}"' for t in signal.sample_titles) or "- (no titles available)"
    )

    return f"""You are designing {target_count} starter prompts for the empty-state of a
shopping assistant powered by Reddit community recommendations.

Indexed communities (most active first):
{subreddit_lines}

Recent post titles from those communities:
{title_lines}

Generate exactly {target_count} starter prompts a real customer might type
into THIS assistant, where every prompt:
- Maps to a product category that the indexed communities actually cover
- Is 6-12 words, action-oriented, and specific
- Avoids topics absent from the indexed communities
- Mixes framings: budget-conscious, gift-finding, comparison, recommendation,
  alternative-to-popular-brand
- Is a question or imperative the user would say (no meta commentary)

Return strictly valid JSON in this shape, with no prose, no markdown fences,
no commentary outside the JSON:

{{"prompts": ["...", "...", ..., "..."]}}
"""


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
    rng: Optional[random.Random] = None,
    raw_items: Optional[Sequence[dict]] = None,
    llm_provider: LLMProvider = LLMProvider.DEEPSEEK,
) -> SuggestedPrompts:
    """Run the full grounding → LLM → parse → persist pipeline.

    Raises if the regenerated set is too small to publish; callers should let
    the cron fail loudly so SNS alarms fire — leaving the previous record in
    place keeps the homepage populated until the next run.
    """
    signal = collect_grounding_signal(items=raw_items, rng=rng)
    if signal.is_empty:
        raise RuntimeError(
            "No grounding signal available; aborting prompt regeneration"
        )

    user_prompt = build_llm_prompt(signal)
    llm = LLMFactory.create_llm(provider=llm_provider)
    response_text = llm.chat(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You output strictly valid JSON. No prose outside the "
                    "JSON object. No markdown code fences."
                ),
            ),
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
        sources_used=[name for name, _ in signal.subreddit_counts],
    )
    record.save()
    logger.info(
        "Persisted SuggestedPrompts",
        extra={
            "count": len(record.prompts),
            "sources": record.sources_used,
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
