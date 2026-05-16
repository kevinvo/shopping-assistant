"""Smoke test for the topic-shift bug captured in production logs.

Replays the gift → cards → backpacks conversation against the real LLM
(via whatever provider is configured) and prints rewrite + HyDE outputs
at each turn. Asserts that the Turn 3 HyDE — the one that exposed the
contamination on prod — does NOT carry gift-domain keywords.

Run:
    cd chalice_app
    python -m scripts.check_topic_shift

Exits 0 on clean (HyDE for "tell me about backpacks" stays on-topic),
non-zero with a diff if gift terms still appear.
"""

from __future__ import annotations

import sys
from typing import List, Tuple

from chalicelib.llm import LLMFactory, LLMProvider
from chalicelib.models.data_objects import ChatMessage

# Keywords that should NEVER appear in the Turn 3 HyDE for "tell me about
# backpacks". These are the exact tokens captured from prod CloudWatch on
# 2026-05-16 that proved gift-context bleed.
GIFT_BLEED_KEYWORDS = (
    "jewelry",
    "keepsake",
    "spa",
    "candle",
    "perfume",
    "chocolate",
    "flowers",
    "handwritten letter",
    "gift box",
    "subscription",
    "personalized",
    "engraved",
    "sentimental",
    "custom artwork",
    "scrapbook",
)


def _format_history(history: List[ChatMessage]) -> str:
    if not history:
        return "  (empty)"
    return "\n".join(f"  {m.role}: {m.content[:80]}" for m in history)


def _turn(
    llm,
    label: str,
    user_msg: str,
    history: List[ChatMessage],
    canned_assistant_reply: str,
) -> Tuple[str, str]:
    print(f"\n=== {label} ===")
    print(f"User: {user_msg}")
    print("History fed to LLM:")
    print(_format_history(history))

    rewritten = llm.rewrite_query(
        last_message_content=user_msg, message_history=history
    )
    hyde = llm.generate_hyde(last_message_content=user_msg, message_history=history)

    print(f"Rewritten: {rewritten}")
    print(f"HyDE:      {hyde}")

    history.append(ChatMessage(role="user", content=user_msg))
    history.append(ChatMessage(role="assistant", content=canned_assistant_reply))

    return rewritten, hyde or ""


def main() -> int:
    llm = LLMFactory.create_llm(provider=LLMProvider.DEEPSEEK)
    history: List[ChatMessage] = []

    _turn(
        llm,
        "Turn 1 — establish gift topic",
        "I want to buy a birthday's gift for my gf",
        history,
        canned_assistant_reply=(
            "Here are some thoughtful gift ideas: jewelry, spa gift card, "
            "personalized keepsake box, coffee subscription..."
        ),
    )

    _turn(
        llm,
        "Turn 2 — topic ellipsis (should CONTINUE)",
        "What about birthday cards?",
        history,
        canned_assistant_reply=(
            "Birthday card ideas: handmade pop-up cards, scratch-off love "
            "notes, custom Spotify plaques..."
        ),
    )

    rewritten_3, hyde_3 = _turn(
        llm,
        "Turn 3 — TOPIC SHIFT (this is the regression bait)",
        "tell me about backpacks",
        history,
        canned_assistant_reply="(not used)",
    )

    print("\n=== Verdict ===")
    bleed_hits = [k for k in GIFT_BLEED_KEYWORDS if k in hyde_3.lower()]
    if bleed_hits:
        print(
            f"FAIL: HyDE for 'tell me about backpacks' still carries "
            f"gift-domain keywords: {bleed_hits}"
        )
        print(f"  HyDE output: {hyde_3}")
        return 1

    print(f"PASS: HyDE stayed on topic. Output: {hyde_3}")
    if "backpack" not in hyde_3.lower():
        print(
            "  WARN: 'backpack' is not in the HyDE output even though gift "
            "bleed is absent. Output may be too generic to drive search."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
