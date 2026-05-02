"""Unit tests for the suggested-prompts generation pipeline."""

import json
import random

import pytest

from chalicelib.services import suggested_prompts as sp


def _items(*pairs):
    return [{"subreddit": s, "title": t} for s, t in pairs]


# ---------------------------------------------------------------------------
# collect_grounding_signal — distribution + ranking
# ---------------------------------------------------------------------------


def test_collect_grounding_ranks_subreddits_by_count():
    items = _items(
        ("BuyItForLife", "boots"),
        ("BuyItForLife", "knives"),
        ("BuyItForLife", "pens"),
        ("malefashionadvice", "jeans"),
        ("malefashionadvice", "denim"),
        ("HeadphoneAdvice", "iems"),
    )
    signal = sp.collect_grounding_signal(items=items, top_k=3, sample_k=10)

    assert [name for name, _ in signal.subreddit_counts] == [
        "BuyItForLife",
        "malefashionadvice",
        "HeadphoneAdvice",
    ]
    assert dict(signal.subreddit_counts)["BuyItForLife"] == 3


def test_collect_grounding_caps_top_k():
    # 5 distinct subreddits, top_k=2 → only the two heaviest survive
    items = _items(*[(f"sub{i}", f"t{i}") for i in range(5)])
    items += _items(("sub0", "x"), ("sub0", "y"), ("sub1", "x"))
    signal = sp.collect_grounding_signal(items=items, top_k=2, sample_k=5)

    assert len(signal.subreddit_counts) == 2
    assert {name for name, _ in signal.subreddit_counts} == {"sub0", "sub1"}


def test_collect_grounding_titles_only_from_top_subreddits():
    items = _items(
        ("popular", "title-A"),
        ("popular", "title-B"),
        ("popular", "title-C"),
        ("rare", "ignored-D"),
    )
    rng = random.Random(0)
    signal = sp.collect_grounding_signal(items=items, top_k=1, sample_k=10, rng=rng)

    assert signal.sample_titles == ["title-A", "title-B", "title-C"] or set(
        signal.sample_titles
    ) == {"title-A", "title-B", "title-C"}
    assert "ignored-D" not in signal.sample_titles


def test_collect_grounding_skips_blank_subreddit():
    items = _items(("", "blank"), ("real", "ok"))
    signal = sp.collect_grounding_signal(items=items, top_k=5, sample_k=5)

    assert [name for name, _ in signal.subreddit_counts] == ["real"]


# ---------------------------------------------------------------------------
# build_llm_prompt
# ---------------------------------------------------------------------------


def test_build_llm_prompt_embeds_communities_and_titles():
    signal = sp.GroundingSignal(
        subreddit_counts=[("BuyItForLife", 12), ("HeadphoneAdvice", 7)],
        sample_titles=["Best $100 chef's knife?", "Looking for waterproof boots"],
    )
    prompt = sp.build_llm_prompt(signal, target_count=24)

    assert "BuyItForLife (12 posts)" in prompt
    assert "HeadphoneAdvice (7 posts)" in prompt
    assert "Best $100 chef's knife?" in prompt
    assert "exactly 24 starter prompts" in prompt


def test_build_llm_prompt_handles_empty_signal_gracefully():
    signal = sp.GroundingSignal(subreddit_counts=[], sample_titles=[])
    prompt = sp.build_llm_prompt(signal)

    assert "(no community signal available)" in prompt
    assert "(no titles available)" in prompt


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
    items = _items(
        ("BuyItForLife", "Best $100 chef's knife"),
        ("BuyItForLife", "Waterproof hiking boots that last"),
        ("HeadphoneAdvice", "Budget alternative to QC45?"),
        ("HeadphoneAdvice", "IEMs under $200"),
    )
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

    record = sp.regenerate_prompts(raw_items=items, rng=random.Random(0))

    assert record.prompts[:20] == [
        f"Solid prompt number {i} for shoppers" for i in range(20)
    ]
    assert record.sources_used  # at least one source recorded
    assert len(saved) == 1


def test_regenerate_prompts_aborts_when_signal_empty(monkeypatch):
    monkeypatch.setattr(
        sp.LLMFactory,
        "create_llm",
        lambda *a, **k: pytest.fail("LLM should not be called"),
    )

    with pytest.raises(RuntimeError):
        sp.regenerate_prompts(raw_items=[])


def test_regenerate_prompts_aborts_when_too_few_pass_validation(monkeypatch):
    items = _items(("BuyItForLife", "title"))
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
        sp.regenerate_prompts(raw_items=items)

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
