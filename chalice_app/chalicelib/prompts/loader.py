"""Hub-overridable prompt loader.

Each prompt module (personas, query_processing, evaluation) keeps its
constant as the documented baseline -- those constants are what code
reviewers see in git and what the bot falls back to if LangSmith Hub
is unreachable. At runtime, `get_prompt()` pulls the latest version
from Hub, caches it for `_CACHE_TTL_SECONDS`, and returns the live
text + a version id we tag onto the @traceable call so each LangSmith
trace records which prompt version produced it.

The cache TTL trades freshness for latency: shorter means rollouts
land faster, longer means fewer Hub round trips. 60 s is the right
balance for our request volume -- most invocations hit the cache,
prompt changes propagate in about a minute, Hub is touched only
~once per minute per warm Lambda container.

Hub failure modes (network, auth, prompt not found) log a warning and
return the baked-in fallback with version=`PROMPT_FALLBACK_VERSION`.
The cache is NOT updated on failure, so the next request retries Hub.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Tuple

from langsmith import Client

from chalicelib.core.config import config

logger = logging.getLogger(__name__)

PROMPT_FALLBACK_VERSION = "fallback"

_CACHE_TTL_SECONDS = 60


@dataclass
class _CachedPrompt:
    text: str
    version: str
    fetched_at: float


_cache: dict[str, _CachedPrompt] = {}
_cache_lock = threading.Lock()
_client: Client | None = None
_client_lock = threading.Lock()


def _get_client() -> Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Client(
                    api_key=config.langsmith_api_key,
                    api_url=config.langsmith_api_url,
                )
    return _client


def _extract_text(template) -> str:
    """ChatPromptTemplate -> the raw string of its first message.

    We push each prompt as a single-message ChatPromptTemplate, so the
    structure is always one SystemMessagePromptTemplate or
    UserMessagePromptTemplate wrapping a PromptTemplate. The raw text
    lives on .prompt.template.
    """
    msg = template.messages[0]
    return msg.prompt.template


def get_prompt(hub_name: str, fallback: str) -> Tuple[str, str]:
    """Return (text, version) for the given Hub prompt.

    `text` is the live Hub content if the pull succeeded, otherwise
    the `fallback` (the baked-in constant in the calling prompt
    module). `version` is the Hub commit hash (12-char prefix) on
    success, or `PROMPT_FALLBACK_VERSION` on failure -- tag this onto
    your @traceable metadata so every trace records which prompt
    version it used.
    """
    now = time.time()
    cached = _cache.get(hub_name)
    if cached and now - cached.fetched_at < _CACHE_TTL_SECONDS:
        return cached.text, cached.version

    try:
        client = _get_client()
        template = client.pull_prompt(hub_name, include_model=False)
        text = _extract_text(template)
        commit = client.pull_prompt_commit(hub_name)
        version = commit.commit_hash[:12]
    except Exception as e:
        logger.warning(
            "prompt loader: pull %s failed, using fallback. error=%s",
            hub_name,
            e,
        )
        return fallback, PROMPT_FALLBACK_VERSION

    with _cache_lock:
        _cache[hub_name] = _CachedPrompt(text=text, version=version, fetched_at=now)
    return text, version
