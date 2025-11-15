"""WebSocket handlers for real-time chat communication."""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from chalice import WebsocketDisconnectedError

from chalicelib.core.structured_logging import LogExtra
from chalicelib.models.data_objects import MessagePayload, ResponsePayload


logger = logging.getLogger()
logger.setLevel(logging.INFO)


sqs_client = boto3.client("sqs")


def handle_websocket_connect(
    connection_id: str,
    skip_db_write: bool = False,
    domain_name: Optional[str] = None,
    stage: Optional[str] = None,
) -> None:
    """Handle WebSocket connection logic."""
    logger.info("WebSocket connect event for connection: %s", connection_id)

    if skip_db_write:
        logger.info(
            "Keep-warm ping detected, skipping DynamoDB write for connection: %s",
            connection_id,
        )

        if domain_name and stage:
            try:
                _send_websocket_message(
                    connection_id, domain_name, stage, {"type": "pong"}
                )
                logger.info(
                    "Sent pong response to keep-warm ping for connection: %s",
                    connection_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to send pong response to keep-warm ping: %s", exc
                )
        else:
            logger.warning(
                "Keep-warm ping detected but domain_name/stage not provided, cannot send pong"
            )
        return

    from chalicelib.aws.dynamo.tables import ConnectionInfo

    session_id = str(uuid.uuid4())
    logger.info("Generated session ID: %s", session_id)

    ttl = int((datetime.now() + timedelta(days=1)).timestamp())
    connection_info = ConnectionInfo(
        id=connection_id,
        ttl=ttl,
        connected_at=datetime.now().isoformat(),
        chat_history=[],
        session_id=session_id,
    )
    connection_info.save()

    logger.info("Successfully stored connection: %s", connection_id)


def handle_websocket_disconnect(connection_id: str) -> None:
    """Handle WebSocket disconnection logic."""
    from chalicelib.aws.dynamo.tables import ConnectionInfo

    logger.info("WebSocket disconnect event for connection: %s", connection_id)
    ConnectionInfo.delete_by_id(id=connection_id)
    logger.info("Successfully removed connection: %s", connection_id)


def _send_websocket_message(
    connection_id: str, domain_name: str, stage: str, message: dict
) -> None:
    """Send a message via API Gateway Management API."""

    gateway_api = boto3.client(
        "apigatewaymanagementapi", endpoint_url=f"https://{domain_name}/{stage}"
    )
    logger.info(
        "Preparing to send WebSocket payload",
        extra=LogExtra(
            connection_id=connection_id,
            domain_name=domain_name,
            stage=stage,
            message_type=message.get("type"),
        ).to_dict(),
    )
    try:
        gateway_api.post_to_connection(
            ConnectionId=connection_id, Data=json.dumps(message).encode("utf-8")
        )
        logger.info("Successfully sent message to connection %s", connection_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "GoneException":
            logger.warning("Connection %s is no longer valid", connection_id)
            raise WebsocketDisconnectedError(
                f"Connection {connection_id} is no longer active"
            )
        logger.error("Error sending message: %s", exc, exc_info=True)
        raise


def handle_websocket_message(
    connection_id: str,
    message_body: dict,
    app,
    domain_name: Optional[str] = None,
    stage: Optional[str] = None,
) -> dict:
    """Handle WebSocket message logic."""

    logger.info("WebSocket message from connection %s", connection_id)
    logger.info("Message body: %s", message_body)

    domain_name = domain_name or os.environ.get("WEBSOCKET_DOMAIN", "unknown")
    stage = stage or os.environ.get("WEBSOCKET_STAGE", "chalice-test")
    logger.info(
        "Resolved WebSocket target",
        extra=LogExtra(
            connection_id=connection_id, domain_name=domain_name, stage=stage
        ).to_dict(),
    )

    if message_body.get("type") == "ping":
        logger.info(
            "Received ping from connection %s, responding with pong", connection_id
        )
        try:
            _send_websocket_message(connection_id, domain_name, stage, {"type": "pong"})
            return {"statusCode": 200}
        except WebsocketDisconnectedError:
            return {"statusCode": 410}
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Error sending pong: %s", exc, exc_info=True)
            return {"statusCode": 500}

    message_content = message_body.get("content")
    if not message_content:
        logger.warning("No message content provided from connection %s", connection_id)
        return {"statusCode": 400}

    request_id = str(uuid.uuid4())
    processing_response = ResponsePayload.create_processing(request_id=request_id)

    try:
        _send_websocket_message(
            connection_id, domain_name, stage, processing_response.to_dict()
        )
    except WebsocketDisconnectedError:
        logger.warning(
            "Connection %s disconnected before processing response", connection_id
        )
        return {"statusCode": 410}
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Error sending processing response: %s", exc, exc_info=True)
        return {"statusCode": 500}

    message_payload = MessagePayload.create(
        connection_id=connection_id,
        domain_name=domain_name,
        stage=stage,
        message=message_content,
        request_id=request_id,
    )
    logger.info(
        "Created message payload for processing",
        extra=LogExtra(
            connection_id=connection_id,
            request_id=request_id,
            stage=stage,
            domain_name=domain_name,
        ).to_dict(),
    )

    queue_url = os.environ.get("CHAT_PROCESSING_QUEUE_URL")
    if queue_url:
        try:
            response = sqs_client.send_message(
                QueueUrl=queue_url, MessageBody=message_payload.to_json()
            )
            logger.info(
                "Message sent to SQS for processing",
                extra=LogExtra(
                    request_id=request_id,
                    connection_id=connection_id,
                    sqs_message_id=response.get("MessageId"),
                ).to_dict(),
            )
        except Exception:  # pragma: no cover - defensive logging
            logger.error(
                "Failed to publish message to SQS",
                extra=LogExtra(
                    request_id=request_id,
                    connection_id=connection_id,
                    queue_url=queue_url,
                ).to_dict(),
                exc_info=True,
            )
            error_response = ResponsePayload.create_error(
                request_id=request_id,
                content="Failed to enqueue request for processing",
            )
            try:
                _send_websocket_message(
                    connection_id, domain_name, stage, error_response.to_dict()
                )
            except WebsocketDisconnectedError:
                logger.warning(
                    "Connection disconnected while sending SQS failure notification",
                    extra=LogExtra(
                        connection_id=connection_id, request_id=request_id
                    ).to_dict(),
                )
            except Exception:  # pragma: no cover - defensive logging
                logger.error(
                    "Unexpected error sending SQS failure notification",
                    extra=LogExtra(
                        connection_id=connection_id, request_id=request_id
                    ).to_dict(),
                    exc_info=True,
                )
            return {"statusCode": 500}
    else:
        logger.error("CHAT_PROCESSING_QUEUE_URL not configured")
        error_response = ResponsePayload.create_error(
            request_id=request_id, content="Service configuration error"
        )
        try:
            app.websocket_api.send(connection_id, json.dumps(error_response.to_dict()))
        except WebsocketDisconnectedError:
            pass
        return {"statusCode": 500}

    return {"statusCode": 200}
