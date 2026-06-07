"""Unit tests for deploy-time event-source-mapping self-healing.

Regression guard: the evaluator SQS trigger was silently left in the ``Disabled``
state and no deploy ever re-enabled it, because ``ensure_post_deploy`` only
checked the ``chat_processor`` mapping. These tests pin the generalized
``ensure_mapping_enabled`` helper and verify ``ensure_post_deploy`` guards *both*
SQS consumer mappings.
"""

from unittest.mock import MagicMock, patch

import pytest

from scripts.deploy import DeployError, ensure_mapping_enabled, ensure_post_deploy


def _client_returning_states(*states):
    """Build a mock Lambda client whose successive ``list_event_source_mappings``
    calls report each state in turn; the final state repeats for extra polls."""
    client = MagicMock()
    responses = [{"EventSourceMappings": [{"UUID": "u-1", "State": s}]} for s in states]

    def _list(**_kwargs):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    client.list_event_source_mappings.side_effect = _list
    return client


@patch("scripts.deploy.time.sleep", return_value=None)
def test_enabled_mapping_is_noop(_sleep):
    client = _client_returning_states("Enabled")
    ensure_mapping_enabled(client, "fn")
    client.update_event_source_mapping.assert_not_called()


@patch("scripts.deploy.time.sleep", return_value=None)
def test_disabled_mapping_gets_reenabled(_sleep):
    client = _client_returning_states("Disabled", "Enabled")
    ensure_mapping_enabled(client, "fn")
    client.update_event_source_mapping.assert_called_once_with(UUID="u-1", Enabled=True)


@patch("scripts.deploy.time.sleep", return_value=None)
def test_disabled_that_never_enables_raises(_sleep):
    client = _client_returning_states("Disabled")  # stays Disabled on every poll
    with pytest.raises(DeployError, match="did not reach Enabled"):
        ensure_mapping_enabled(client, "fn", max_polls=3)
    client.update_event_source_mapping.assert_called_once()


@patch("scripts.deploy.time.sleep", return_value=None)
def test_missing_mapping_raises(_sleep):
    client = MagicMock()
    client.list_event_source_mappings.return_value = {"EventSourceMappings": []}
    with pytest.raises(DeployError, match="No event-source mapping"):
        ensure_mapping_enabled(client, "fn")


@patch("scripts.deploy.time.sleep", return_value=None)
def test_unexpected_state_raises(_sleep):
    client = _client_returning_states("Creating")
    with pytest.raises(DeployError, match="is 'Creating'"):
        ensure_mapping_enabled(client, "fn")


def _post_deploy_mocks(mock_boto_client):
    """Wire boto3.client so sqs reports an empty backlog and lambda is a stub."""
    sqs = MagicMock()
    sqs.get_queue_attributes.return_value = {
        "Attributes": {
            "ApproximateNumberOfMessages": "0",
            "ApproximateNumberOfMessagesNotVisible": "0",
        }
    }
    lambda_client = MagicMock()
    mock_boto_client.side_effect = lambda service, **_kw: (
        sqs if service == "sqs" else lambda_client
    )


@patch("scripts.deploy.ensure_mapping_enabled")
@patch("scripts.deploy.boto3.client")
def test_ensure_post_deploy_guards_both_mappings(mock_boto_client, mock_ensure):
    """The core regression: both chat_processor AND evaluator must be guarded
    when both queues are configured."""
    _post_deploy_mocks(mock_boto_client)

    ensure_post_deploy(
        stage="chalice-test",
        env_vars={
            "CHAT_PROCESSING_QUEUE_URL": "https://example/chat-queue",
            "EVALUATION_QUEUE_URL": "https://example/eval-queue",
        },
        region="ap-southeast-1",
        max_visible=10,
        max_inflight=20,
    )

    guarded = {c.args[1] for c in mock_ensure.call_args_list}
    assert "shopping-assistant-api-chalice-test-chat_processor" in guarded
    assert "shopping-assistant-api-chalice-test-evaluator" in guarded


@patch("scripts.deploy.ensure_mapping_enabled")
@patch("scripts.deploy.boto3.client")
def test_ensure_post_deploy_skips_unconfigured_handler(mock_boto_client, mock_ensure):
    """A handler whose queue URL isn't set is skipped, not failed — a deploy on a
    stage without the evaluator must not be blocked by its absent mapping."""
    _post_deploy_mocks(mock_boto_client)

    ensure_post_deploy(
        stage="chalice-test",
        env_vars={"CHAT_PROCESSING_QUEUE_URL": "https://example/chat-queue"},
        region="ap-southeast-1",
        max_visible=10,
        max_inflight=20,
    )

    guarded = {c.args[1] for c in mock_ensure.call_args_list}
    assert "shopping-assistant-api-chalice-test-chat_processor" in guarded
    assert "shopping-assistant-api-chalice-test-evaluator" not in guarded
