"""Chat processor for handling WebSocket chat messages."""

import json
import os
import boto3
import logging
from botocore.exceptions import ClientError
from typing import Dict, Any, Union, Optional

from chalicelib.sessions.chat_session_manager import Chat
from chalicelib.models.data_objects import (
    MessagePayload,
    ResponsePayload,
    EvaluationMessage,
)
from chalicelib.aws.dynamo.tables import ConnectionInfo
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.core.structured_logging import LogExtra

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs_client = boto3.client("sqs")
EVALUATION_QUEUE_URL = os.environ.get("EVALUATION_QUEUE_URL")


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

    gateway_api = boto3.client(
        "apigatewaymanagementapi", endpoint_url=f"https://{domain_name}/{stage}"
    )

    try:
        gateway_api.post_to_connection(
            ConnectionId=connection_id, Data=json.dumps(message_dict).encode("utf-8")
        )
        logger.info(f"Successfully sent message to connection {connection_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "GoneException":
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


def process_message(message_payload: MessagePayload) -> None:
    """Process a chat message and send response via WebSocket."""
    connection_id = message_payload.connection_id
    domain_name = message_payload.domain_name
    stage = message_payload.stage
    user_message = message_payload.message
    request_id = message_payload.request_id

    logger.info(
        "Processing chat request",
        extra=LogExtra(
            request_id=request_id,
            connection_id=connection_id,
            domain_name=domain_name,
            stage=stage,
        ).to_dict(),
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
        logger.info(
            "Fetching connection info",
            extra=LogExtra(
                connection_id=connection_id, request_id=request_id
            ).to_dict(),
        )
        connection_info = get_connection_info(connection_id=connection_id)
        if connection_info is None:
            logger.warning(
                f"Connection {connection_id} not found - likely disconnected. "
                f"Skipping message processing for request {request_id}"
            )
            # Don't try to send error message to non-existent connection
            return
        logger.info(
            "Loaded connection info",
            extra=LogExtra(
                connection_id=connection_id,
                request_id=request_id,
                chat_history_length=len(connection_info.chat_history),
            ).to_dict(),
        )

        logger.info(
            "Invoking chat pipeline",
            extra=LogExtra(
                request_id=request_id,
                connection_id=connection_id,
            ).to_dict()
            | {
                "session_id": getattr(connection_info, "session_id", None),
                "history_sample": [
                    h.to_dict() for h in connection_info.chat_history[-3:]
                ],
            },
        )

        accumulated_response = []
        connection_valid = [True]
        messageId = request_id
        start_sent = [False]

        def streaming_callback(chunk: str) -> None:
            if not connection_valid[0]:
                return

            current_connection_info = get_connection_info(connection_id=connection_id)
            if current_connection_info is None:
                logger.warning(
                    f"Connection {connection_id} disconnected during streaming, "
                    f"stopping chunk delivery for request {request_id}"
                )
                connection_valid[0] = False
                return

            try:
                if not start_sent[0]:
                    start_response = ResponsePayload.create_message_start(
                        request_id=request_id, messageId=messageId
                    )
                    send_message(
                        connection_id=connection_id,
                        domain_name=domain_name,
                        stage=stage,
                        message=start_response,
                    )
                    start_sent[0] = True

                chunk_response = ResponsePayload.create_message_chunk(
                    request_id=request_id, content=chunk, messageId=messageId
                )
                send_message(
                    connection_id=connection_id,
                    domain_name=domain_name,
                    stage=stage,
                    message=chunk_response,
                )
                accumulated_response.append(chunk)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "GoneException":
                    logger.warning(
                        f"Connection {connection_id} disconnected during streaming chunk send, "
                        f"stopping chunk delivery for request {request_id}"
                    )
                    connection_valid[0] = False
                else:
                    logger.error(
                        f"Error sending streaming chunk: {str(e)}",
                        exc_info=True,
                    )
                accumulated_response.append(chunk)
            except Exception as chunk_error:
                logger.error(
                    f"Error sending streaming chunk: {str(chunk_error)}",
                    exc_info=True,
                )
                accumulated_response.append(chunk)

        response_message, updated_chat_history, eval_metadata = Chat().process_chat(
            query=user_message,
            session_id=connection_id,
            chat_history=[hist.to_dict() for hist in connection_info.chat_history],
            socket_id=connection_id,
            request_id=request_id,
            streaming_callback=streaming_callback,
        )

        if accumulated_response:
            full_response = "".join(accumulated_response)
        else:
            full_response = response_message

        connection_info.chat_history = updated_chat_history
        persist_connection_info(connection_info=connection_info)

        if not connection_valid[0]:
            logger.warning(
                f"Connection {connection_id} was disconnected during streaming, "
                f"skipping completion message for request {request_id}"
            )
        else:
            current_connection_info = get_connection_info(connection_id=connection_id)
            if current_connection_info is None:
                logger.warning(
                    f"Connection {connection_id} disconnected before completion, "
                    f"skipping completion message for request {request_id}"
                )
            else:
                try:
                    if start_sent[0]:
                        end_response = ResponsePayload.create_message_end(
                            request_id=request_id, messageId=messageId
                        )
                        send_message(
                            connection_id=connection_id,
                            domain_name=domain_name,
                            stage=stage,
                            message=end_response,
                        )
                except ClientError as e:
                    if e.response.get("Error", {}).get("Code") == "GoneException":
                        logger.warning(
                            f"Connection {connection_id} disconnected while sending completion "
                            f"for request {request_id}"
                        )
                    else:
                        logger.error(
                            f"Error sending streaming complete: {str(e)}",
                            exc_info=True,
                        )
                except Exception as complete_error:
                    logger.error(
                        f"Error sending streaming complete: {str(complete_error)}",
                        exc_info=True,
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
