"""Unit tests for human thumbs feedback → LangSmith.

Covers the service validation/posting and that the LangSmith run_id is carried
on the message_end payload so the frontend can attach feedback to it.
"""

from unittest.mock import MagicMock, patch

import pytest

from chalicelib.services import feedback as fb
from chalicelib.models.data_objects import MessageType, ResponsePayload


# ---------------------------------------------------------------------------
# submit_human_feedback — validation + posting
# ---------------------------------------------------------------------------


@patch("chalicelib.services.feedback._get_client")
def test_thumbs_up_posts_score_1(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client
    run_id = "019e948d-ec1a-73d8-8253-08a3fdadc0b2"

    fb.submit_human_feedback(run_id=run_id, score=1)

    client.create_feedback.assert_called_once_with(
        run_id=run_id, key="user_feedback", score=1.0
    )


@patch("chalicelib.services.feedback._get_client")
def test_thumbs_down_posts_score_0(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client
    run_id = "019e948d-ec1a-73d8-8253-08a3fdadc0b2"

    fb.submit_human_feedback(run_id=run_id, score=0)

    client.create_feedback.assert_called_once_with(
        run_id=run_id, key="user_feedback", score=0.0
    )


@patch("chalicelib.services.feedback._get_client")
def test_string_score_is_coerced(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client

    fb.submit_human_feedback(run_id="019e948d-ec1a-73d8-8253-08a3fdadc0b2", score="1")

    assert client.create_feedback.call_args.kwargs["score"] == 1.0


@patch("chalicelib.services.feedback._get_client")
def test_non_uuid_run_id_rejected_without_posting(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client

    with pytest.raises(fb.InvalidFeedback, match="not a valid UUID"):
        fb.submit_human_feedback(run_id="not-a-uuid", score=1)

    client.create_feedback.assert_not_called()


@patch("chalicelib.services.feedback._get_client")
def test_out_of_range_score_rejected(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client

    with pytest.raises(fb.InvalidFeedback, match="must be"):
        fb.submit_human_feedback(
            run_id="019e948d-ec1a-73d8-8253-08a3fdadc0b2", score=0.5
        )

    client.create_feedback.assert_not_called()


@patch("chalicelib.services.feedback._get_client")
def test_missing_score_rejected(mock_get_client):
    client = MagicMock()
    mock_get_client.return_value = client

    with pytest.raises(fb.InvalidFeedback):
        fb.submit_human_feedback(
            run_id="019e948d-ec1a-73d8-8253-08a3fdadc0b2", score=None
        )

    client.create_feedback.assert_not_called()


# ---------------------------------------------------------------------------
# ResponsePayload.create_message_end — run_id plumbing
# ---------------------------------------------------------------------------


def test_message_end_carries_run_id_when_set():
    payload = ResponsePayload.create_message_end(
        request_id="req-1", messageId="m-1", run_id="run-123"
    )
    d = payload.to_dict()

    assert d["type"] == MessageType.MESSAGE_END
    assert d["run_id"] == "run-123"


def test_message_end_omits_run_id_when_absent():
    payload = ResponsePayload.create_message_end(request_id="req-1", messageId="m-1")

    assert "run_id" not in payload.to_dict()


def test_response_payload_round_trips_run_id():
    payload = ResponsePayload.create_message_end(
        request_id="req-1", messageId="m-1", run_id="run-123"
    )
    restored = ResponsePayload.from_dict(payload.to_dict())

    assert restored.run_id == "run-123"
