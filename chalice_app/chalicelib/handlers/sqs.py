"""SQS message handlers for async processing."""

import logging
import json

from chalicelib.error_notifications import notify_on_exception

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Note: lambda_client no longer needed - all handlers run directly in Chalice


def register_sqs_handlers(app):
    """Register all SQS handlers with the Chalice app."""

    @app.on_sqs_message(queue="ChatProcessingQueue", batch_size=1)
    @notify_on_exception
    def chat_processor(event):
        """
        Process chat messages from SQS queue.

        Now uses the migrated Chat class and chat processor directly.
        """
        try:
            from chalicelib.chat_message_service import process_message
            from chalicelib.data_objects import MessagePayload

            # For each SQS record, process the message directly
            record_count = 0
            for record in event:
                record_count += 1
                try:
                    # Parse the message body into MessagePayload
                    message_body_json = record.body
                    message_payload = MessagePayload.from_dict(
                        json.loads(message_body_json)
                    )

                    # Process the message directly (no Lambda invocation needed)
                    process_message(message_payload)
                    logger.info("Successfully processed chat message")

                except Exception as e:
                    logger.error(
                        f"Error processing chat message: {str(e)}", exc_info=True
                    )

            logger.info(f"Received {record_count} SQS messages for chat processing")
        except Exception as e:
            logger.error(f"Error in chat_processor: {str(e)}", exc_info=True)
            raise

    @app.on_sqs_message(queue="shopping-assistant-evaluation-queue", batch_size=10)
    @notify_on_exception
    def evaluator(event):
        """
        Process evaluation tasks from SQS queue.

        Now runs directly in Chalice (no Lambda invocation needed).
        """
        try:
            from chalicelib.handlers.background.jobs.evaluator_handler import (
                process_evaluation_task,
            )
            from chalicelib.data_objects import EvaluationMessage

            processed = 0
            failed = 0

            for record in event:
                try:
                    eval_message = EvaluationMessage.from_dict(json.loads(record.body))
                    process_evaluation_task(eval_message=eval_message)
                    processed += 1
                except Exception as e:
                    logger.error(
                        f"Error processing evaluation: {str(e)}", exc_info=True
                    )
                    failed += 1

            logger.info(
                f"Evaluation batch complete: {processed} processed, {failed} failed (received {processed + failed} total)"
            )

        except Exception as e:
            logger.error(f"Error in evaluator: {str(e)}", exc_info=True)
            raise
