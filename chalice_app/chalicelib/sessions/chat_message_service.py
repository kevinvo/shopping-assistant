"""Chat processor for handling WebSocket chat messages."""

import json
import os
import boto3
import logging
from botocore.exceptions import ClientError
from functools import lru_cache
from typing import Dict, Any, Union, Optional, Callable

from chalicelib.sessions.chat_session_manager import Chat
from chalicelib.models.data_objects import (
    MessagePayload,
    ResponsePayload,
    EvaluationMessage,
)
from chalicelib.aws.dynamo.tables import ConnectionInfo, ConversationHistory
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.core.structured_logging import LogExtra

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs_client = boto3.client("sqs")
EVALUATION_QUEUE_URL = os.environ.get("EVALUATION_QUEUE_URL")


# Module-level lazy singleton. Building Chat() per request rebuilds the
# QdrantIndexer (Qdrant client, OpenAIEmbeddings, S3 client), DeepSeekClient,
# and BM25Reranker -- discarding all their HTTP/TLS pools and the in-memory
# BM25 vocab cache. Reusing one instance across warm invocations is the
# single biggest fix for warm TTFT after the streaming buffer.
_chat_instance: Optional[Chat] = None


def get_chat() -> Chat:
    global _chat_instance
    if _chat_instance is None:
        _chat_instance = Chat()
    return _chat_instance


@lru_cache(maxsize=8)
def _apigw_client_for(domain_name: str, stage: str):
    # endpoint_url depends on (domain_name, stage), so we can't use a single
    # module-level client. lru_cache amortizes the ~10-30ms boto3 client
    # construction across the ~25 sends per streamed response.
    return boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=f"https://{domain_name}/{stage}",
    )


# Buffer LLM-streamed tokens until this many characters accumulate, then flush
# in one APIGW post_to_connection. Targets ~20-30 websocket sends per response
# (down from one-per-token, which dominated streaming latency). The very first
# chunk is flushed eagerly so the user sees text before the buffer fills.
STREAM_CHUNK_FLUSH_CHARS = 64

# API Gateway raises ClientError with this code when the websocket peer has
# disconnected. Promoted to a constant so the three handlers stay in sync.
GONE_EXCEPTION = "GoneException"


@measure_execution_time
def send_message(
    *,
    connection_id: str,
    domain_name: str,
    stage: str,
    message: Union[Dict[str, Any], ResponsePayload],
) -> None:
    """Send a message to a WebSocket connection."""
    message_type = None
    if isinstance(message, ResponsePayload):
        message_type = message.type
    elif isinstance(message, dict):
        message_type = message.get("type")

    logger.info(
        "Attempting to send message",
        extra=LogExtra(
            connection_id=connection_id,
            domain_name=domain_name,
            stage=stage,
            message_type=message_type,
        ).to_dict(),
    )

    # Convert ResponsePayload to dict if needed
    if isinstance(message, ResponsePayload):
        message_dict = message.to_dict()
        logger.info(f"Message content (ResponsePayload): {message_dict}")
    else:
        message_dict = message
        logger.info(f"Message content (Dict): {message_dict}")

    gateway_api = _apigw_client_for(domain_name, stage)

    try:
        gateway_api.post_to_connection(
            ConnectionId=connection_id, Data=json.dumps(message_dict).encode("utf-8")
        )
        logger.info(f"Successfully sent message to connection {connection_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == GONE_EXCEPTION:
            logger.warning(f"Connection {connection_id} is no longer valid")
        else:
            logger.error(f"Error sending message: {str(e)}", exc_info=True)
            raise


@measure_execution_time
def trigger_async_evaluation(
    query: str,
    response: str,
    session_id: str,
    request_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Send evaluation task to SQS queue (fire-and-forget)."""

    if not EVALUATION_QUEUE_URL:
        logger.warning("EVALUATION_QUEUE_URL not set, skipping evaluation")
        return

    try:
        if metadata is None:
            metadata = {}

        evaluation_message = EvaluationMessage(
            query=query,
            response=response,
            session_id=session_id,
            request_id=request_id,
            metadata=metadata,
        )

        sqs_client.send_message(
            QueueUrl=EVALUATION_QUEUE_URL,
            MessageBody=json.dumps(evaluation_message.to_dict()),
            DelaySeconds=0,  # Run evaluator immediately
        )

        logger.info(f"Queued evaluation for request {request_id}")

    except Exception as e:
        # Never fail user request due to evaluation
        logger.warning(f"Failed to queue evaluation: {e}")


def handle_chat_error(message_payload: MessagePayload, error: Exception) -> None:
    logger.error(f"Error processing chat: {str(error)}", exc_info=True)

    # Check if connection still exists before trying to send error
    connection_info = ConnectionInfo.get_by_id(id=message_payload.connection_id)
    if connection_info is None:
        logger.warning(
            f"Connection {message_payload.connection_id} no longer exists. "
            "Skipping error message send."
        )
        return

    # Create an error response payload
    error_response = ResponsePayload.create_error(request_id=message_payload.request_id)

    try:
        # Send error message back to client
        send_message(
            connection_id=message_payload.connection_id,
            domain_name=message_payload.domain_name,
            stage=message_payload.stage,
            message=error_response,
        )
    except Exception as send_error:
        logger.error(
            f"Failed to send error message: {str(send_error)}",
            exc_info=True,
        )


@measure_execution_time
def get_connection_info(connection_id: str) -> Optional[ConnectionInfo]:
    return ConnectionInfo.get_by_id(id=connection_id)


@measure_execution_time
def persist_connection_info(connection_info: ConnectionInfo) -> None:
    connection_info.save()


@measure_execution_time
def load_or_create_conversation_history(
    session_id: str, conversation_id: str
) -> ConversationHistory:
    history = ConversationHistory.get(
        session_id=session_id, conversation_id=conversation_id
    )
    if history is None:
        history = ConversationHistory.new(
            session_id=session_id, conversation_id=conversation_id
        )
    return history


@measure_execution_time
def persist_conversation_history(history: ConversationHistory) -> None:
    history.bump_expiry()
    history.save()


def process_message(message_payload: MessagePayload) -> None:
    """Process a chat message and send response via WebSocket."""
    connection_id = message_payload.connection_id
    domain_name = message_payload.domain_name
    stage = message_payload.stage
    user_message = message_payload.message
    request_id = message_payload.request_id
    session_id = message_payload.session_id
    conversation_id = message_payload.conversation_id or "default"

    logger.info(
        "Processing chat request",
        extra=LogExtra(
            request_id=request_id,
            connection_id=connection_id,
            domain_name=domain_name,
            stage=stage,
        ).to_dict()
        | {"session_id": session_id, "conversation_id": conversation_id},
    )
    logger.info(
        "Inbound chat payload",
        extra=LogExtra(
            request_id=request_id,
            connection_id=connection_id,
        ).to_dict()
        | {
            "message_preview": user_message[:200],
            "timestamp": message_payload.timestamp,
        },
    )

    if not all([connection_id, domain_name, stage, user_message]):
        logger.error(f"Missing required fields in message payload: {message_payload}")
        return

    try:
        connection_info = get_connection_info(connection_id=connection_id)
        if connection_info is None:
            logger.warning(
                f"Connection {connection_id} not found - likely disconnected. "
                f"Skipping message processing for request {request_id}"
            )
            return

        if not session_id:
            session_id = getattr(connection_info, "session_id", None) or ""
            logger.warning(
                "MessagePayload missing session_id; rolled back to ConnectionInfo "
                "session_id=%s for request %s",
                session_id,
                request_id,
            )

        if not session_id:
            logger.error(
                "Cannot resolve session_id for request %s — dropping message",
                request_id,
            )
            return

        history = load_or_create_conversation_history(
            session_id=session_id, conversation_id=conversation_id
        )
        logger.info(
            "Loaded conversation history",
            extra=LogExtra(
                connection_id=connection_id,
                request_id=request_id,
                chat_history_length=len(history.messages),
            ).to_dict()
            | {"session_id": session_id, "conversation_id": conversation_id},
        )

        accumulated_response = []
        connection_valid = [True]
        messageId = request_id
        start_sent = [False]
        first_flush_done = [False]
        chunk_buffer: list[str] = []

        def safe_invoke(
            op: Callable[[], None], *, context: str, flip_on_error: bool = True
        ) -> bool:
            """Run a websocket-bound op, swallowing send failures.

            Returns True on success. On any failure (Gone or otherwise) the
            error is logged and connection_valid is flipped so subsequent
            token callbacks short-circuit instead of looping into the same
            error.
            """
            try:
                op()
                return True
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code == GONE_EXCEPTION:
                    logger.warning(
                        f"Connection {connection_id} gone during {context} "
                        f"for request {request_id}"
                    )
                else:
                    logger.error(f"Error during {context}: {str(e)}", exc_info=True)
            except Exception as e:
                logger.error(f"Error during {context}: {str(e)}", exc_info=True)
            if flip_on_error:
                connection_valid[0] = False
            return False

        def flush_chunk_buffer() -> None:
            if not chunk_buffer:
                return
            combined = "".join(chunk_buffer)
            chunk_buffer.clear()
            accumulated_response.append(combined)
            first_flush_done[0] = True
            chunk_response = ResponsePayload.create_message_chunk(
                request_id=request_id,
                content=combined,
                messageId=messageId,
                conversationId=conversation_id,
            )
            send_message(
                connection_id=connection_id,
                domain_name=domain_name,
                stage=stage,
                message=chunk_response,
            )

        def streaming_callback(chunk: str) -> None:
            if not connection_valid[0]:
                # Capture for full-response logging even though the client is
                # gone; the model already produced these tokens.
                accumulated_response.append(chunk)
                return

            if not start_sent[0]:
                ok = safe_invoke(
                    lambda: send_message(
                        connection_id=connection_id,
                        domain_name=domain_name,
                        stage=stage,
                        message=ResponsePayload.create_message_start(
                            request_id=request_id,
                            messageId=messageId,
                            conversationId=conversation_id,
                        ),
                    ),
                    context="streaming start",
                )
                if not ok:
                    return
                start_sent[0] = True

            chunk_buffer.append(chunk)
            # Flush the very first chunk eagerly so the user sees text
            # immediately; coalesce subsequent chunks up to the threshold.
            if (
                not first_flush_done[0]
                or sum(map(len, chunk_buffer)) >= STREAM_CHUNK_FLUSH_CHARS
            ):
                safe_invoke(flush_chunk_buffer, context="streaming chunk")

        response_message, updated_chat_history, eval_metadata = get_chat().process_chat(
            query=user_message,
            session_id=session_id,
            chat_history=[hist.to_dict() for hist in history.messages],
            socket_id=connection_id,
            request_id=request_id,
            streaming_callback=streaming_callback,
        )

        if connection_valid[0] and chunk_buffer:
            safe_invoke(flush_chunk_buffer, context="streaming final flush")

        if accumulated_response:
            full_response = "".join(accumulated_response)
        else:
            full_response = response_message

        history.messages = updated_chat_history
        persist_conversation_history(history=history)

        if not connection_valid[0]:
            logger.warning(
                f"Connection {connection_id} was disconnected during streaming, "
                f"skipping completion message for request {request_id}"
            )
        elif start_sent[0]:
            # flip_on_error=False: we're past streaming, the flag is no
            # longer consulted, and we don't want a transient error here
            # to look like a disconnect in the post-flow logs.
            safe_invoke(
                lambda: send_message(
                    connection_id=connection_id,
                    domain_name=domain_name,
                    stage=stage,
                    message=ResponsePayload.create_message_end(
                        request_id=request_id,
                        messageId=messageId,
                        conversationId=conversation_id,
                    ),
                ),
                context="streaming complete",
                flip_on_error=False,
            )

        logger.info(
            "Generated chat response",
            extra=LogExtra(
                request_id=request_id,
                connection_id=connection_id,
                updated_chat_history_length=len(updated_chat_history),
            ).to_dict(),
        )
        logger.info(
            "Chat response details",
            extra=LogExtra(
                request_id=request_id,
                connection_id=connection_id,
            ).to_dict()
            | {
                "response_preview": full_response[:200],
                "evaluation_keys": list((eval_metadata or {}).keys()),
            },
        )

        logger.info(f"Successfully sent streaming response for request {request_id}")

        # Trigger async evaluation (after user receives response)
        # Use full_response which includes all accumulated chunks
        trigger_async_evaluation(
            query=user_message,
            response=full_response,
            session_id=connection_id,
            request_id=request_id,
            metadata=eval_metadata,
        )
        logger.info(
            "Queued evaluation task",
            extra=LogExtra(
                request_id=request_id,
                connection_id=connection_id,
                evaluation_metadata_keys=list((eval_metadata or {}).keys()),
            ).to_dict(),
        )

    except Exception as chat_error:
        logger.error(f"Error processing chat: {str(chat_error)}", exc_info=True)
        handle_chat_error(message_payload=message_payload, error=chat_error)
