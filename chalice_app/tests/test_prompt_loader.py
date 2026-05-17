"""Unit tests for the LangSmith Hub prompt loader.

We stub out the LangSmith Client so these tests run offline. The real
Hub fetch is exercised by the live smoke (`scripts/check_topic_shift.py`
and the prod battery) once the loader is wired into a prompt.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chalicelib.prompts import loader
from chalicelib.prompts.loader import PROMPT_FALLBACK_VERSION, get_prompt


@pytest.fixture(autouse=True)
def reset_loader_state():
    """Each test starts with an empty cache + no cached client."""
    loader._cache.clear()
    loader._client = None
    yield
    loader._cache.clear()
    loader._client = None


def _fake_template(text: str) -> MagicMock:
    """Mimic a ChatPromptTemplate with one message wrapping the given text."""
    tpl = MagicMock()
    tpl.messages = [MagicMock()]
    tpl.messages[0].prompt.template = text
    return tpl


def _fake_commit(commit_hash: str) -> MagicMock:
    commit = MagicMock()
    commit.commit_hash = commit_hash
    return commit


def test_returns_hub_text_and_commit_hash():
    client = MagicMock()
    client.pull_prompt.return_value = _fake_template("hub text")
    client.pull_prompt_commit.return_value = _fake_commit("a" * 40)

    with patch.object(loader, "_get_client", return_value=client):
        text, version = get_prompt("any", fallback="baked")

    assert text == "hub text"
    # Version is the 12-char prefix of the commit hash.
    assert version == "a" * 12


def test_falls_back_when_pull_fails():
    client = MagicMock()
    client.pull_prompt.side_effect = RuntimeError("network down")

    with patch.object(loader, "_get_client", return_value=client):
        text, version = get_prompt("any", fallback="baked")

    assert text == "baked"
    assert version == PROMPT_FALLBACK_VERSION


def test_cache_hit_skips_hub_on_second_call():
    client = MagicMock()
    client.pull_prompt.return_value = _fake_template("hub text")
    client.pull_prompt_commit.return_value = _fake_commit("b" * 40)

    with patch.object(loader, "_get_client", return_value=client):
        get_prompt("any", fallback="baked")
        get_prompt("any", fallback="baked")

    assert client.pull_prompt.call_count == 1
    assert client.pull_prompt_commit.call_count == 1


def test_cache_expires_after_ttl():
    client = MagicMock()
    client.pull_prompt.return_value = _fake_template("hub text")
    client.pull_prompt_commit.return_value = _fake_commit("c" * 40)

    with patch.object(loader, "_get_client", return_value=client):
        get_prompt("any", fallback="baked")
        # Manually expire the cache entry.
        cached = loader._cache["any"]
        cached.fetched_at -= loader._CACHE_TTL_SECONDS + 1
        get_prompt("any", fallback="baked")

    assert client.pull_prompt.call_count == 2


def test_failed_pull_does_not_poison_cache():
    """A Hub error should NOT cache the fallback -- next call retries Hub."""
    client = MagicMock()
    client.pull_prompt.side_effect = [
        RuntimeError("down"),
        _fake_template("hub recovered"),
    ]
    client.pull_prompt_commit.return_value = _fake_commit("d" * 40)

    with patch.object(loader, "_get_client", return_value=client):
        first_text, first_version = get_prompt("any", fallback="baked")
        second_text, second_version = get_prompt("any", fallback="baked")

    assert first_text == "baked"
    assert first_version == PROMPT_FALLBACK_VERSION
    assert second_text == "hub recovered"
    assert second_version == "d" * 12


def test_different_prompt_names_cached_separately():
    client = MagicMock()
    client.pull_prompt.side_effect = lambda name, include_model=False: _fake_template(
        f"text for {name}"
    )
    client.pull_prompt_commit.side_effect = lambda name: _fake_commit(name * 40)

    with patch.object(loader, "_get_client", return_value=client):
        text_a, _ = get_prompt("a", fallback="fa")
        text_b, _ = get_prompt("b", fallback="fb")
        # Second pull of "a" should hit cache.
        text_a2, _ = get_prompt("a", fallback="fa")

    assert text_a == "text for a"
    assert text_b == "text for b"
    assert text_a2 == "text for a"
    # 2 cold fetches + 1 cache hit = 2 pull_prompt calls
    assert client.pull_prompt.call_count == 2
