"""Tests for progressive PROCESSING status events in Chat.process_chat."""

from unittest.mock import MagicMock, patch

import pytest

from chalicelib.sessions.chat_session_manager import (
    Chat,
    STATUS_READING,
    STATUS_SEARCHING,
    STATUS_WRITING,
)


@pytest.fixture
def chat():
    """Chat with all external deps replaced by Mocks; pipeline internals stubbed."""
    with (
        patch("chalicelib.sessions.chat_session_manager.IndexerFactory") as idx_factory,
        patch("chalicelib.sessions.chat_session_manager.LLMFactory") as llm_factory,
        patch("chalicelib.sessions.chat_session_manager.BM25Reranker") as reranker_cls,
    ):
        llm = MagicMock()
        llm.rewrite_query.return_value = "rewritten"
        llm.generate_hyde.return_value = ""  # skips hyde_search branch
        llm.stream_chat.return_value = "final response"
        llm_factory.create_llm.return_value = llm

        reranker = MagicMock()
        reranker.get_relevance_scores.return_value = []
        reranker_cls.return_value = reranker

        idx_factory.create_indexer.return_value = MagicMock()

        instance = Chat()

        instance._perform_search = MagicMock(return_value=[])
        instance._combine_search_results = MagicMock(return_value=[])
        instance._prepare_results_for_metrics = MagicMock(return_value=[])
        instance._rerank_results = MagicMock(return_value=[])
        instance._build_context = MagicMock(return_value="")
        instance._prepare_chat_history = MagicMock(return_value=[])
        instance._generate_response = MagicMock(return_value="final response")

        yield instance


def test_emits_three_status_events_in_order(chat):
    captured = []

    chat.process_chat(
        query="hello",
        session_id="sess-1",
        chat_history=[],
        socket_id="conn-1",
        request_id="req-1",
        status_callback=captured.append,
    )

    assert captured == [STATUS_SEARCHING, STATUS_READING, STATUS_WRITING]


def test_omitting_status_callback_is_fine(chat):
    """Pipeline must work without a status_callback (existing callers)."""
    chat.process_chat(
        query="hello",
        session_id="sess-1",
        chat_history=[],
        socket_id="conn-1",
        request_id="req-1",
    )


def test_status_callback_exception_does_not_crash(chat):
    """A throwing callback is swallowed — status emit is best-effort."""

    def boom(_text):
        raise RuntimeError("connection vanished")

    chat.process_chat(
        query="hello",
        session_id="sess-1",
        chat_history=[],
        socket_id="conn-1",
        request_id="req-1",
        status_callback=boom,
    )


def test_status_events_arrive_before_generate_response(chat):
    """All 3 status events must fire before the LLM stream starts.

    Otherwise the trace line never shows up during the latency we care about.
    """
    captured = []
    chat._generate_response = MagicMock(
        side_effect=lambda **_: captured.append("__generate__") or "final"
    )

    chat.process_chat(
        query="hello",
        session_id="sess-1",
        chat_history=[],
        socket_id="conn-1",
        request_id="req-1",
        status_callback=captured.append,
    )

    assert captured.index(STATUS_SEARCHING) < captured.index("__generate__")
    assert captured.index(STATUS_READING) < captured.index("__generate__")
    assert captured.index(STATUS_WRITING) < captured.index("__generate__")
