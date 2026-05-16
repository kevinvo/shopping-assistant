"""Unit tests for topic-shift handling in rewrite_query and generate_hyde.

These tests do not call any LLM. They verify:
1. Prompt sentinels (so the topic-shift few-shot examples don't silently
   get edited away).
2. History tail trimming (so a long conversation can't bury the new
   query's topic signal under accumulated context).

Real LLM behavior is exercised by chalice_app/scripts/check_topic_shift.py.
"""

from typing import Any, Dict, List

from chalicelib.llm.client import (
    BaseLLM,
    _HYDE_HISTORY_TAIL,
    _REWRITE_HISTORY_TAIL,
)
from chalicelib.models.data_objects import ChatMessage
from chalicelib.prompts import (
    CONTEXT_AWARE_PROMPT_REWRITING,
    HYDE_SYSTEM_PROMPT,
)


class _CapturingLLM(BaseLLM):
    """Minimal BaseLLM that records what would have been sent."""

    def __init__(self) -> None:
        self.captured_messages: List[ChatMessage] = []
        self.captured_kwargs: Dict[str, Any] = {}

    def chat(self, messages: List[ChatMessage], **kwargs: Any) -> str:
        self.captured_messages = messages
        self.captured_kwargs = kwargs
        if kwargs.get("json_mode"):
            return '{"rewritten_query": "captured"}'
        return "captured, keywords, list"


def _make_history(n: int) -> List[ChatMessage]:
    """Alternating user/assistant messages, easy to identify by content."""
    return [
        ChatMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"history-{i}",
        )
        for i in range(n)
    ]


def _history_slice(messages: List[ChatMessage]) -> List[ChatMessage]:
    """Drop the leading system frame and the trailing user prompt; what's
    left is the conversation history the LLM actually sees."""
    return messages[1:-1]


# ---------------------------------------------------------------------------
# Prompt sentinels — break if someone edits away the topic-shift handling
# ---------------------------------------------------------------------------


def test_rewrite_prompt_declares_two_cases():
    assert "CONTINUATION" in CONTEXT_AWARE_PROMPT_REWRITING
    assert "TOPIC SHIFT" in CONTEXT_AWARE_PROMPT_REWRITING


def test_rewrite_prompt_keeps_backpack_anchor_example():
    """The gift→backpack example is the canonical regression we're guarding."""
    assert "backpacks" in CONTEXT_AWARE_PROMPT_REWRITING.lower()
    assert "gift" in CONTEXT_AWARE_PROMPT_REWRITING.lower()


def test_rewrite_prompt_biases_toward_topic_shift_when_in_doubt():
    """The "when in doubt" rule is load-bearing for small-model behavior."""
    assert "when in doubt" in CONTEXT_AWARE_PROMPT_REWRITING.lower()


def test_hyde_prompt_declares_two_cases():
    assert "CONTINUATION" in HYDE_SYSTEM_PROMPT
    assert "TOPIC SHIFT" in HYDE_SYSTEM_PROMPT


def test_hyde_prompt_keeps_backpack_anchor_example():
    assert "backpacks" in HYDE_SYSTEM_PROMPT.lower()
    assert "gift" in HYDE_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# History tail trimming
# ---------------------------------------------------------------------------


def test_rewrite_query_trims_long_history_to_tail():
    llm = _CapturingLLM()
    history = _make_history(20)

    llm.rewrite_query(last_message_content="ignored", message_history=history)

    history_seen = _history_slice(llm.captured_messages)
    assert len(history_seen) <= _REWRITE_HISTORY_TAIL


def test_rewrite_query_passes_short_history_unchanged():
    llm = _CapturingLLM()
    history = _make_history(2)

    llm.rewrite_query(last_message_content="ignored", message_history=history)

    history_seen = _history_slice(llm.captured_messages)
    assert len(history_seen) <= len(history)


def test_hyde_query_trims_long_history_to_tail():
    llm = _CapturingLLM()
    history = _make_history(20)

    llm.generate_hyde(last_message_content="ignored", message_history=history)

    history_seen = _history_slice(llm.captured_messages)
    assert len(history_seen) <= _HYDE_HISTORY_TAIL


def test_rewrite_query_handles_empty_history():
    llm = _CapturingLLM()
    llm.rewrite_query(last_message_content="hello", message_history=[])
    # Sanity: at least the user prompt was sent.
    assert llm.captured_messages
    assert llm.captured_messages[-1].role == "user"


def test_hyde_query_handles_empty_history():
    llm = _CapturingLLM()
    llm.generate_hyde(last_message_content="hello", message_history=[])
    assert llm.captured_messages
    assert llm.captured_messages[-1].role == "user"


# ---------------------------------------------------------------------------
# JSON-mode wiring — rewrite must request structured output
# ---------------------------------------------------------------------------


def test_rewrite_query_requests_json_mode():
    llm = _CapturingLLM()
    llm.rewrite_query(last_message_content="hello", message_history=[])
    assert llm.captured_kwargs.get("json_mode") is True


def test_hyde_does_not_request_json_mode():
    """HyDE outputs a comma-separated keyword phrase, not JSON; forcing
    JSON mode here costs 100-300 ms of TTFT for no benefit."""
    llm = _CapturingLLM()
    llm.generate_hyde(last_message_content="hello", message_history=[])
    assert not llm.captured_kwargs.get("json_mode")
