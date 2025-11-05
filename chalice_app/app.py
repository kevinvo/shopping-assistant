"""
Main entry point for Chalice application.

This module initializes the Chalice app and registers all handlers.
"""

from chalice import Chalice, Cron, Rate  # type: ignore[import]
import logging
import json
import os
from datetime import datetime, timezone

import boto3

# Import all handlers and WebSocket functions at module level
from chalicelib.handlers.rest import register_rest_routes
from chalicelib.handlers.websocket import (
    handle_websocket_connect,
    handle_websocket_disconnect,
    handle_websocket_message,
)
from chalicelib.error_notifications import notify_on_exception

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Chalice application
app = Chalice(app_name="shopping-assistant-api")

# Enable experimental WebSocket support
app.experimental_feature_flags.update(["WEBSOCKETS"])

step_function_client = boto3.client("stepfunctions")
SCRAPER_STATE_MACHINE_ARN = os.environ.get("SCRAPER_STATE_MACHINE_ARN")

register_rest_routes(app)


@app.lambda_function(name="scraper_worker")
@notify_on_exception
def scraper_worker(event, context):
    """Execute the daily Reddit scraper. Intended for Step Functions invocation."""
    try:
        logger.info("Scraper worker invoked", extra={"event": event})
        from chalicelib.handlers.background.jobs.scraper_handler import (
            run_daily_scraper,
        )

        result = run_daily_scraper()
        logger.info("Scraper worker completed", extra={"result": result})
        return result
    except Exception:  # pragma: no cover - defensive logging for prod
        logger.error("Scraper worker failed", exc_info=True)
        raise


# WebSocket handlers must be defined at module level in app.py for Chalice to find them
@app.on_ws_connect()
@notify_on_exception
def websocket_connect(event):
    """Handle WebSocket connection"""
    try:
        connection_id = event.connection_id
        handle_websocket_connect(connection_id)
    except Exception as e:
        logger.error(f"Error in websocket_connect: {str(e)}", exc_info=True)


@app.on_ws_disconnect()
@notify_on_exception
def websocket_disconnect(event):
    """Handle WebSocket disconnection"""
    try:
        connection_id = event.connection_id
        handle_websocket_disconnect(connection_id)
    except Exception as e:
        logger.error(f"Error in websocket_disconnect: {str(e)}", exc_info=True)


@app.on_ws_message()
@notify_on_exception
def websocket_message(event):
    """Handle WebSocket messages"""
    try:
        connection_id = event.connection_id
        message_body = json.loads(event.body)
        domain_name = getattr(event, "domain_name", None)
        stage = getattr(event, "stage", None)

        if not domain_name:
            domain_name = (
                event.context.get("domainName") if hasattr(event, "context") else None
            )
        if not stage:
            stage = event.context.get("stage") if hasattr(event, "context") else None

        return handle_websocket_message(
            connection_id=connection_id,
            message_body=message_body,
            app=app,
            domain_name=domain_name,
            stage=stage,
        )
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in message: {str(e)}")
        return {"statusCode": 400}
    except Exception as e:
        logger.error(f"Error in websocket_message: {str(e)}", exc_info=True)
        return {"statusCode": 500}


# SQS handlers must be defined at module level for Chalice to discover them
@app.on_sqs_message(queue="ChatProcessingQueue", batch_size=1)
@notify_on_exception
def chat_processor(event):
    """Process chat messages from SQS queue."""
    try:
        # event is an SQSEvent object - iterate directly
        from chalicelib.chat_message_service import process_message
        from chalicelib.data_objects import MessagePayload

        record_count = 0
        for record in event:
            record_count += 1
            try:
                logger.info(
                    "Received SQS record",
                    extra={
                        "message_id": getattr(record, "message_id", None),
                        "body_length": (
                            len(record.body) if getattr(record, "body", None) else 0
                        ),
                        "attributes": getattr(record, "attributes", {}),
                    },
                )
                # record.body is already a string, not JSON
                message_payload = MessagePayload.from_dict(json.loads(record.body))
                process_message(message_payload)
                logger.info("Successfully processed chat message")
            except Exception as e:
                logger.error(f"Error processing chat message: {str(e)}", exc_info=True)

        logger.info(f"Received {record_count} SQS messages for chat processing")
    except Exception as e:
        logger.error(f"Error in chat_processor: {str(e)}", exc_info=True)
        raise


@app.on_sqs_message(queue="shopping-assistant-evaluation-queue", batch_size=10)
@notify_on_exception
def evaluator(event):
    """Process evaluation tasks from SQS queue."""
    try:
        # event is an SQSEvent object - iterate directly
        from chalicelib.handlers.background.jobs.evaluator_handler import (
            process_evaluation_task,
        )
        from chalicelib.data_objects import EvaluationMessage

        processed = 0
        failed = 0

        for record in event:
            try:
                # record.body is already a string
                eval_message = EvaluationMessage.from_dict(json.loads(record.body))
                process_evaluation_task(eval_message=eval_message)
                processed += 1
            except Exception as e:
                logger.error(f"Error processing evaluation: {str(e)}", exc_info=True)
                failed += 1

        logger.info(
            f"Evaluation batch complete: {processed} processed, {failed} failed (received {processed + failed} total)"
        )
    except Exception as e:
        logger.error(f"Error in evaluator: {str(e)}", exc_info=True)
        raise


@app.schedule(Cron(0, 2, "*", "*", "?", "*"))
@notify_on_exception
def scraper(event):
    """
    Daily Reddit scraper - runs at 2:00 AM UTC
    """
    try:
        if not SCRAPER_STATE_MACHINE_ARN:
            raise RuntimeError(
                "SCRAPER_STATE_MACHINE_ARN environment variable is not set"
            )

        execution_name = "scraper-" + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%f"
        )
        logger.info(
            "Starting scraper Step Functions execution",
            extra={
                "state_machine_arn": SCRAPER_STATE_MACHINE_ARN,
                "execution_name": execution_name,
            },
        )

        response = step_function_client.start_execution(
            stateMachineArn=SCRAPER_STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps({"trigger": "scheduled"}),
        )

        logger.info(
            "Scraper Step Function started",
            extra={"executionArn": response.get("executionArn")},
        )

    except Exception as e:
        logger.error(f"Error in scraper: {str(e)}", exc_info=True)
        raise


@app.schedule(Cron(0, 3, "*", "*", "?", "*"))
@notify_on_exception
def indexer(event):
    """
    Daily Reddit data indexer - runs at midnight UTC
    """
    try:
        logger.info("Starting daily Reddit indexing job")

        from chalicelib.handlers.background.jobs.indexer_handler import (
            run_daily_indexer,
        )

        result = run_daily_indexer()
        logger.info(f"Indexer completed: {result.get('statusCode')}")

    except Exception as e:
        logger.error(f"Error in indexer: {str(e)}", exc_info=True)
        raise


@app.schedule(Cron(0, 0, "*", "*", "?", "*"))
@notify_on_exception
def glue_starter(event):
    """
    Daily Glue job starter - runs at midnight UTC

    Starts the Glue job for processing Reddit data.
    Now runs directly in Chalice.
    """
    try:
        logger.info("Starting daily Glue job")

        from chalicelib.handlers.background.jobs.glue_handler import start_glue_job

        result = start_glue_job()
        logger.info(f"Glue job started: {result.get('statusCode')}")

    except Exception as e:
        logger.error(f"Error in glue_starter: {str(e)}", exc_info=True)
        raise


@app.schedule(Rate(7, unit=Rate.DAYS))
@notify_on_exception
def layer_cleanup(event):
    """Weekly cleanup for Lambda layer artifacts stored in S3."""
    try:
        from chalicelib.handlers.background.jobs.layer_cleanup_handler import (
            LayerCleanupConfig,
            cleanup_old_layer_artifacts,
        )

        bucket_name = os.environ["LAYER_ARTIFACTS_BUCKET_NAME"]
        prefix = os.environ.get("LAYER_ARTIFACTS_PREFIX", "")
        retention_days = int(os.environ.get("LAYER_ARTIFACTS_RETENTION_DAYS", "30"))
        min_versions = int(os.environ.get("LAYER_ARTIFACTS_MIN_VERSIONS", "5"))

        config = LayerCleanupConfig(
            bucket_name=bucket_name,
            prefix=prefix,
            retention_days=retention_days,
            min_versions_to_keep=min_versions,
        )
        result = cleanup_old_layer_artifacts(config)

        logger.info(
            "Layer artifact cleanup finished",
            extra={"result": result},
        )

    except KeyError as exc:
        logger.error(
            "Missing required environment variable for layer cleanup",
            extra={"variable": str(exc)},
        )
        raise
    except Exception as exc:
        logger.error(f"Error in layer_artifacts_cleanup: {exc}", exc_info=True)
        raise
