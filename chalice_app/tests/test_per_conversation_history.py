"""Unit tests for Phase 2 per-conversation chat history."""

from datetime import datetime, timedelta
from typing import List
from unittest.mock import patch

import pytest

from chalicelib.aws.dynamo.tables import (
    CONVERSATION_HISTORY_TTL_DAYS,
    ConversationHistory,
    conversation_history_id,
)
from chalicelib.models.data_objects import (
    ChatMessage,
    MessagePayload,
    MessageType,
    ResponsePayload,
)


# ---------------------------------------------------------------------------
# MessagePayload — new fields round-trip
# ---------------------------------------------------------------------------


def test_message_payload_round_trip_with_new_fields():
    payload = MessagePayload.create(
        connection_id="conn-1",
        domain_name="example.com",
        stage="chalice-test",
        message="hello",
        request_id="req-1",
        session_id="sess-1",
        conversation_id="conv-1",
        message_id="msg-1",
    )

    as_dict = payload.to_dict()
    rebuilt = MessagePayload.from_dict(as_dict)

    assert rebuilt.session_id == "sess-1"
    assert rebuilt.conversation_id == "conv-1"
    assert rebuilt.message_id == "msg-1"
    assert rebuilt.connection_id == "conn-1"
    assert rebuilt.message == "hello"


def test_message_payload_from_dict_tolerates_missing_new_fields():
    """Old SQS payloads (pre Phase 2) must still deserialize."""
    legacy_dict = {
        "connection_id": "conn-1",
        "domain_name": "example.com",
        "stage": "chalice-test",
        "message": "hi",
        "request_id": "req-1",
        "timestamp": "2026-01-01T00:00:00",
    }

    rebuilt = MessagePayload.from_dict(legacy_dict)

    assert rebuilt.session_id == ""
    assert rebuilt.conversation_id == ""
    assert rebuilt.message_id == ""


# ---------------------------------------------------------------------------
# ResponsePayload — conversationId echoed only when set
# ---------------------------------------------------------------------------


def test_response_payload_emits_conversation_id_when_set():
    resp = ResponsePayload.create_message_chunk(
        request_id="req-1",
        content="abc",
        messageId="msg-1",
        conversationId="conv-1",
    )
    serialized = resp.to_dict()

    assert serialized["type"] == MessageType.MESSAGE_CHUNK
    assert serialized["conversationId"] == "conv-1"
    assert serialized["messageId"] == "msg-1"


def test_response_payload_omits_conversation_id_when_absent():
    resp = ResponsePayload.create_message_chunk(
        request_id="req-1", content="abc", messageId="msg-1"
    )
    serialized = resp.to_dict()

    assert "conversationId" not in serialized
    assert serialized["messageId"] == "msg-1"


# ---------------------------------------------------------------------------
# ConversationHistory — composite key + TTL
# ---------------------------------------------------------------------------


def test_conversation_history_id_composite():
    assert conversation_history_id("sess-1", "conv-1") == "sess-1#conv-1"


def test_conversation_history_new_sets_composite_id_and_ttl():
    history = ConversationHistory.new(session_id="sess-1", conversation_id="conv-1")

    assert history.id == "sess-1#conv-1"
    assert history.session_id == "sess-1"
    assert history.conversation_id == "conv-1"
    assert history.messages == []

    expiry = datetime.fromtimestamp(history.expiry_time)
    expected = datetime.now() + timedelta(days=CONVERSATION_HISTORY_TTL_DAYS)
    # Allow 5-second skew for test execution time
    assert abs((expiry - expected).total_seconds()) < 5


def test_conversation_history_bump_expiry_advances_ttl():
    history = ConversationHistory.new(session_id="s", conversation_id="c")
    history.expiry_time = 0  # simulate stale

    history.bump_expiry()

    expiry = datetime.fromtimestamp(history.expiry_time)
    expected = datetime.now() + timedelta(days=CONVERSATION_HISTORY_TTL_DAYS)
    assert abs((expiry - expected).total_seconds()) < 5


def test_conversation_history_to_item_serializes_messages():
    history = ConversationHistory.new(session_id="s", conversation_id="c")
    history.messages = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="hello"),
    ]

    item = history.to_item()

    assert item["id"] == "s#c"
    assert item["session_id"] == "s"
    assert item["conversation_id"] == "c"
    assert item["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


# ---------------------------------------------------------------------------
# process_message — uses ConversationHistory and falls back when needed
# ---------------------------------------------------------------------------


class _FakeConnectionInfo:
    def __init__(self, connection_id: str, session_id: str = "fallback-sess") -> None:
        self.id = connection_id
        self.session_id = session_id
        self.chat_history: List[ChatMessage] = []


def _build_payload(**overrides) -> MessagePayload:
    base = dict(
        connection_id="conn-1",
        domain_name="example.com",
        stage="chalice-test",
        message="hi there",
        request_id="req-1",
        session_id="sess-1",
        conversation_id="conv-1",
        message_id="msg-1",
    )
    base.update(overrides)
    return MessagePayload.create(**base)


def _patch_chat_to_return(updated_history):
    """Patch Chat().process_chat to return a deterministic result."""

    def _fake_process_chat(*, query, chat_history, streaming_callback=None, **_):
        if streaming_callback:
            streaming_callback("response")
        return "response", updated_history, {"chat_history_length": len(chat_history)}

    return _fake_process_chat


def test_process_message_loads_and_saves_conversation_history(monkeypatch):
    from chalicelib.sessions import chat_message_service

    saved_histories: List[ConversationHistory] = []
    sent_messages: List[dict] = []

    monkeypatch.setattr(
        chat_message_service,
        "get_connection_info",
        lambda connection_id: _FakeConnectionInfo(connection_id=connection_id),
    )
    monkeypatch.setattr(
        chat_message_service,
        "load_or_create_conversation_history",
        lambda session_id, conversation_id: ConversationHistory.new(
            session_id=session_id, conversation_id=conversation_id
        ),
    )
    monkeypatch.setattr(
        chat_message_service,
        "persist_conversation_history",
        lambda history: saved_histories.append(history),
    )
    monkeypatch.setattr(
        chat_message_service,
        "send_message",
        lambda **kwargs: sent_messages.append(kwargs["message"].to_dict()),
    )
    monkeypatch.setattr(
        chat_message_service,
        "trigger_async_evaluation",
        lambda *args, **kwargs: None,
    )

    updated_history = [
        ChatMessage(role="system", content="persona"),
        ChatMessage(role="user", content="hi there"),
        ChatMessage(role="assistant", content="response"),
    ]

    with patch.object(chat_message_service, "Chat") as MockChat:
        MockChat.return_value.process_chat.side_effect = _patch_chat_to_return(
            updated_history
        )
        chat_message_service.process_message(_build_payload())

    assert len(saved_histories) == 1
    saved = saved_histories[0]
    assert saved.id == "sess-1#conv-1"
    assert [m.content for m in saved.messages] == ["persona", "hi there", "response"]

    # Streaming envelope echoes conversationId
    types = [m["type"] for m in sent_messages]
    assert MessageType.MESSAGE_START in types
    assert MessageType.MESSAGE_CHUNK in types
    assert MessageType.MESSAGE_END in types
    chunk = next(m for m in sent_messages if m["type"] == MessageType.MESSAGE_CHUNK)
    assert chunk["conversationId"] == "conv-1"


def test_process_message_falls_back_to_connection_session_id(monkeypatch):
    """When MessagePayload.session_id is missing, use the server-stored one."""
    from chalicelib.sessions import chat_message_service

    loaded_keys: List[tuple] = []

    monkeypatch.setattr(
        chat_message_service,
        "get_connection_info",
        lambda connection_id: _FakeConnectionInfo(
            connection_id=connection_id, session_id="server-sess"
        ),
    )

    def _fake_load(session_id, conversation_id):
        loaded_keys.append((session_id, conversation_id))
        return ConversationHistory.new(
            session_id=session_id, conversation_id=conversation_id
        )

    monkeypatch.setattr(
        chat_message_service, "load_or_create_conversation_history", _fake_load
    )
    monkeypatch.setattr(
        chat_message_service, "persist_conversation_history", lambda history: None
    )
    monkeypatch.setattr(chat_message_service, "send_message", lambda **kwargs: None)
    monkeypatch.setattr(
        chat_message_service, "trigger_async_evaluation", lambda *a, **k: None
    )

    with patch.object(chat_message_service, "Chat") as MockChat:
        MockChat.return_value.process_chat.side_effect = _patch_chat_to_return([])
        chat_message_service.process_message(
            _build_payload(session_id="", conversation_id="conv-1")
        )

    assert loaded_keys == [("server-sess", "conv-1")]


def test_process_message_drops_when_no_session_id_anywhere(monkeypatch):
    """If neither payload nor ConnectionInfo carries session_id, give up safely."""
    from chalicelib.sessions import chat_message_service

    monkeypatch.setattr(
        chat_message_service,
        "get_connection_info",
        lambda connection_id: _FakeConnectionInfo(
            connection_id=connection_id, session_id=""
        ),
    )
    called = []
    monkeypatch.setattr(
        chat_message_service,
        "load_or_create_conversation_history",
        lambda **kwargs: called.append(kwargs) or pytest.fail("must not be called"),
    )

    chat_message_service.process_message(
        _build_payload(session_id="", conversation_id="conv-1")
    )

    assert called == []
