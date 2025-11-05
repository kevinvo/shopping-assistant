"""
WebSocket handlers for real-time chat communication.

This module contains all WebSocket-related handlers and helper functions.
The handlers are defined at module level so Chalice can automatically discover them.
"""

import logging
import json
import uuid
import os
from datetime import datetime, timedelta
from typing import Optional
from chalice import WebsocketDisconnectedError
from chalicelib.dynamo_tables import ConnectionInfo
from chalicelib.data_objects import MessagePayload, ResponsePayload
from chalicelib.logging_utils import LogExtra
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize SQS client
sqs_client = boto3.client("sqs")


def handle_websocket_connect(connection_id: str) -> None:
    """Handle WebSocket connection logic."""
    logger.info(f"WebSocket connect event for connection: {connection_id}")

    # Generate a unique session ID
    session_id = str(uuid.uuid4())
    logger.info(f"Generated session ID: {session_id}")

    # Create connection info with TTL (24 hours)
    ttl = int((datetime.now() + timedelta(days=1)).timestamp())
    connection_info = ConnectionInfo(
        id=connection_id,
        ttl=ttl,
        connected_at=datetime.now().isoformat(),
        chat_history=[],
        session_id=session_id,
    )
    connection_info.save()

    logger.info(f"Successfully stored connection: {connection_id}")


def handle_websocket_disconnect(connection_id: str) -> None:
    """Handle WebSocket disconnection logic."""
    logger.info(f"WebSocket disconnect event for connection: {connection_id}")

    # Remove connection info from DynamoDB
    ConnectionInfo.delete_by_id(id=connection_id)
    logger.info(f"Successfully removed connection: {connection_id}")


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
        logger.info(f"Successfully sent message to connection {connection_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "GoneException":
            logger.warning(f"Connection {connection_id} is no longer valid")
            raise WebsocketDisconnectedError(
                f"Connection {connection_id} is no longer active"
            )
        else:
            logger.error(f"Error sending message: {str(e)}", exc_info=True)
            raise


def handle_websocket_message(
    connection_id: str,
    message_body: dict,
    app,
    domain_name: Optional[str] = None,
    stage: Optional[str] = None,
) -> dict:
    """Handle WebSocket message logic."""
    logger.info(f"WebSocket message from connection {connection_id}")
    logger.info(f"Message body: {message_body}")

    # Get the API Gateway domain and stage from environment
    domain_name = domain_name or os.environ.get("WEBSOCKET_DOMAIN", "unknown")
    stage = stage or os.environ.get("WEBSOCKET_STAGE", "chalice-test")
    logger.info(
        "Resolved WebSocket target",
        extra=LogExtra(
            connection_id=connection_id, domain_name=domain_name, stage=stage
        ).to_dict(),
    )

    # Handle ping messages with a direct pong response
    if message_body.get("type") == "ping":
        logger.info(
            f"Received ping from connection {connection_id}, responding with pong"
        )
        try:
            _send_websocket_message(connection_id, domain_name, stage, {"type": "pong"})
            return {"statusCode": 200}
        except WebsocketDisconnectedError:
            return {"statusCode": 410}
        except Exception as e:
            logger.error(f"Error sending pong: {str(e)}", exc_info=True)
            return {"statusCode": 500}

    message_content = message_body.get("content")
    if not message_content:
        logger.warning(f"No message content provided from connection {connection_id}")
        return {"statusCode": 400}

    # Generate a unique request ID
    request_id = str(uuid.uuid4())

    # Send an immediate response that processing has started
    processing_response = ResponsePayload.create_processing(request_id=request_id)

    try:
        _send_websocket_message(
            connection_id, domain_name, stage, processing_response.to_dict()
        )
    except WebsocketDisconnectedError:
        logger.warning(
            f"Connection {connection_id} disconnected before processing response"
        )
        return {"statusCode": 410}
    except Exception as e:
        logger.error(f"Error sending processing response: {str(e)}", exc_info=True)
        return {"statusCode": 500}

    # Create a MessagePayload instance for SQS
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

    # Send to SQS for processing
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
        except Exception:
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
            except Exception:
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
        # Send error response
        error_response = ResponsePayload.create_error(
            request_id=request_id, content="Service configuration error"
        )
        try:
            app.websocket_api.send(connection_id, json.dumps(error_response.to_dict()))
        except WebsocketDisconnectedError:
            pass
        return {"statusCode": 500}

    return {"statusCode": 200}
