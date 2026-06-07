"""Persist human thumbs feedback to LangSmith.

Kept independent of ``jobs.evaluator`` so the REST Lambda doesn't import the
judge LLM (instantiated at evaluator import time) just to post a thumbs score.

A thumbs vote attaches to the same LangSmith run the async evaluator scores —
so ``user_feedback`` (human) and ``overall_score`` (LLM-as-judge) sit on one
run and can be correlated directly to validate the judge against humans.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from langsmith import Client

logger = logging.getLogger(__name__)

# Feedback key written to LangSmith. Distinct from the evaluator's keys
# (overall_score, faithfulness, …) so the two never collide on a run.
HUMAN_FEEDBACK_KEY = "user_feedback"

# Thumbs map onto the same 0..1 scale the LLM-judge overall_score uses, so the
# human and judge signals are directly comparable on a single run.
THUMBS_DOWN = 0.0
THUMBS_UP = 1.0
_VALID_SCORES = {THUMBS_DOWN, THUMBS_UP}

_client: Optional[Client] = None


class InvalidFeedback(ValueError):
    """Raised when run_id or score fails validation; callers map this to 400."""


def _get_client() -> Client:
    """Lazily build a module-level LangSmith client (reused across warm calls).

    config is imported here, not at module top, so importing this module
    doesn't trigger the Secrets Manager load in AppConfig — keeps the unit
    tests (which patch this function) free of AWS dependencies.
    """
    global _client
    if _client is None:
        from chalicelib.core.config import config

        _client = Client(
            api_key=config.langsmith_api_key,
            api_url=config.langsmith_api_url,
        )
    return _client


def submit_human_feedback(run_id: str, score: object) -> None:
    """Attach a human thumbs score to an existing LangSmith run.

    Validates that ``run_id`` is a UUID and ``score`` is a thumbs value
    (0.0 or 1.0), raising InvalidFeedback otherwise. Re-votes are allowed:
    each call posts a fresh feedback record and LangSmith surfaces the latest.
    """
    try:
        uuid.UUID(str(run_id))
    except (ValueError, AttributeError, TypeError):
        raise InvalidFeedback(f"run_id is not a valid UUID: {run_id!r}")

    try:
        score_value = float(score)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise InvalidFeedback(f"score must be numeric, got {score!r}")

    if score_value not in _VALID_SCORES:
        raise InvalidFeedback(
            f"score must be {THUMBS_DOWN} or {THUMBS_UP}, got {score_value!r}"
        )

    _get_client().create_feedback(
        run_id=run_id,
        key=HUMAN_FEEDBACK_KEY,
        score=score_value,
    )
    logger.info("Posted human feedback for run %s: score=%s", run_id, score_value)
