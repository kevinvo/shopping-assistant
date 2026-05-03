"""Unit tests for the suggested-prompts generation pipeline."""

import json

import pytest

from chalicelib.services import suggested_prompts as sp


# ---------------------------------------------------------------------------
# collect_grounding_signal
# ---------------------------------------------------------------------------


def test_collect_grounding_defaults_to_curated_subreddit_list():
    signal = sp.collect_grounding_signal()

    assert signal.subreddits == sp.INDEXED_SUBREDDITS
    assert not signal.is_empty


def test_collect_grounding_accepts_explicit_list():
    signal = sp.collect_grounding_signal(subreddits=["a", "b", "c"])

    assert signal.subreddits == ["a", "b", "c"]


def test_collect_grounding_strips_blanks_and_whitespace():
    signal = sp.collect_grounding_signal(subreddits=["  buyitforlife ", "", "   "])

    assert signal.subreddits == ["buyitforlife"]


def test_collect_grounding_is_empty_when_all_blank():
    assert sp.collect_grounding_signal(subreddits=["", "  "]).is_empty
    assert sp.collect_grounding_signal(subreddits=[]).is_empty


# ---------------------------------------------------------------------------
# build_llm_prompt
# ---------------------------------------------------------------------------


def test_build_llm_prompt_embeds_subreddits_with_r_prefix():
    signal = sp.GroundingSignal(subreddits=["BuyItForLife", "headphones"])
    prompt = sp.build_llm_prompt(signal, target_count=24)

    assert "- r/BuyItForLife" in prompt
    assert "- r/headphones" in prompt
    assert "exactly 24 starter prompts" in prompt


def test_build_llm_prompt_handles_empty_signal_gracefully():
    signal = sp.GroundingSignal(subreddits=[])
    prompt = sp.build_llm_prompt(signal)

    assert "(no community signal available)" in prompt


# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------


def _valid_payload(n=24):
    prompts = [f"Prompt number {i} for shoppers" for i in range(n)]
    return {"prompts": prompts}


def test_parse_llm_response_bare_json():
    raw = json.dumps(_valid_payload(20))
    parsed = sp.parse_llm_response(raw)

    assert len(parsed) == 20
    assert all(isinstance(p, str) for p in parsed)


def test_parse_llm_response_inside_markdown_fence():
    payload = _valid_payload(18)
    raw = "Here you go:\n```json\n" + json.dumps(payload) + "\n```\nLet me know!"
    parsed = sp.parse_llm_response(raw)

    assert len(parsed) == 18


def test_parse_llm_response_with_leading_prose():
    raw = "Sure! " + json.dumps(_valid_payload(16))
    parsed = sp.parse_llm_response(raw)

    assert len(parsed) == 16


def test_parse_llm_response_filters_too_short_or_too_long():
    raw = json.dumps(
        {
            "prompts": [
                "ok",  # too short
                "a normal-sized prompt here",  # ok
                "x " * 25,  # too long
                "another reasonable prompt for buyers",
            ]
        }
    )
    parsed = sp.parse_llm_response(raw)

    assert len(parsed) == 2
    assert "a normal-sized prompt here" in parsed


def test_parse_llm_response_dedupes_case_insensitive():
    raw = json.dumps(
        {
            "prompts": [
                "Find me a quality starter knife",
                "find me a quality starter knife",  # dup, different case
                "Recommend budget headphones for travel",
            ]
        }
    )
    parsed = sp.parse_llm_response(raw)

    assert len(parsed) == 2


def test_parse_llm_response_raises_on_garbage():
    with pytest.raises(ValueError):
        sp.parse_llm_response("not json at all, sorry")


def test_parse_llm_response_raises_on_missing_prompts_key():
    with pytest.raises(ValueError):
        sp.parse_llm_response(json.dumps({"other": []}))


# ---------------------------------------------------------------------------
# regenerate_prompts — end-to-end with stubbed LLM and DDB
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    def chat(self, *, messages, **kwargs) -> str:
        self.calls.append(messages)
        return self.response


def test_regenerate_prompts_persists_validated_set(monkeypatch):
    payload = json.dumps(
        {"prompts": [f"Solid prompt number {i} for shoppers" for i in range(20)]}
    )
    fake_llm = _FakeLLM(payload)
    monkeypatch.setattr(sp.LLMFactory, "create_llm", lambda *a, **k: fake_llm)

    saved = []
    monkeypatch.setattr(
        sp.SuggestedPrompts,
        "save",
        lambda self: saved.append(self) or self,
    )

    record = sp.regenerate_prompts(subreddits=["buyitforlife", "headphones"])

    assert record.prompts[:20] == [
        f"Solid prompt number {i} for shoppers" for i in range(20)
    ]
    assert record.sources_used == ["buyitforlife", "headphones"]
    assert len(saved) == 1


def test_regenerate_prompts_uses_default_subreddits_when_omitted(monkeypatch):
    payload = json.dumps(
        {"prompts": [f"Solid prompt number {i} for shoppers" for i in range(18)]}
    )
    fake_llm = _FakeLLM(payload)
    monkeypatch.setattr(sp.LLMFactory, "create_llm", lambda *a, **k: fake_llm)
    monkeypatch.setattr(sp.SuggestedPrompts, "save", lambda self: self)

    record = sp.regenerate_prompts()

    assert record.sources_used == sp.INDEXED_SUBREDDITS
    # And the LLM saw the subreddit names embedded in its user prompt.
    user_message = next(
        m for m in fake_llm.calls[0] if getattr(m, "role", None) == "user"
    )
    for name in sp.INDEXED_SUBREDDITS[:5]:
        assert f"- r/{name}" in user_message.content


def test_regenerate_prompts_aborts_when_signal_empty(monkeypatch):
    monkeypatch.setattr(
        sp.LLMFactory,
        "create_llm",
        lambda *a, **k: pytest.fail("LLM should not be called"),
    )

    with pytest.raises(RuntimeError):
        sp.regenerate_prompts(subreddits=[])


def test_regenerate_prompts_aborts_when_too_few_pass_validation(monkeypatch):
    payload = json.dumps(
        {"prompts": [f"prompt number {i}" for i in range(5)]}  # below MIN_ACCEPTABLE
    )
    fake_llm = _FakeLLM(payload)
    monkeypatch.setattr(sp.LLMFactory, "create_llm", lambda *a, **k: fake_llm)

    saved = []
    monkeypatch.setattr(
        sp.SuggestedPrompts, "save", lambda self: saved.append(self) or self
    )

    with pytest.raises(ValueError):
        sp.regenerate_prompts(subreddits=["x"])

    assert saved == []  # never persisted, previous record kept intact


# ---------------------------------------------------------------------------
# load_or_default
# ---------------------------------------------------------------------------


def test_load_or_default_returns_cached_prompts(monkeypatch):
    fake_record = sp.SuggestedPrompts.new(
        prompts=["one cached prompt about boots"], sources_used=["BuyItForLife"]
    )
    monkeypatch.setattr(
        sp.SuggestedPrompts, "load", classmethod(lambda cls: fake_record)
    )

    assert sp.load_or_default() == ["one cached prompt about boots"]


def test_load_or_default_returns_static_fallback_when_empty(monkeypatch):
    monkeypatch.setattr(sp.SuggestedPrompts, "load", classmethod(lambda cls: None))

    result = sp.load_or_default()
    assert result == sp._STATIC_FALLBACK
    assert len(result) >= 4
