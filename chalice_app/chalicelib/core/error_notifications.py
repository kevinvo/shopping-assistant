"""Decorator for notifying on unhandled exceptions."""

from __future__ import annotations

import functools
import logging
import os
import traceback
from typing import Callable, ParamSpec, TypeVar

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

P = ParamSpec("P")
R = TypeVar("R")

_SNS_TOPIC_ARN = os.environ.get("ERROR_ALERT_TOPIC_ARN")


def _publish_trace(event_name: str, trace: str) -> None:
    """Send the traceback to SNS if a topic is configured."""

    if not _SNS_TOPIC_ARN:
        return

    try:
        boto3.client("sns").publish(
            TopicArn=_SNS_TOPIC_ARN,
            Subject=f"[Chalice Error] {event_name}",
            Message=trace,
        )
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.error("Failed to publish error notification: %s", exc, exc_info=True)


def notify_on_exception(func: Callable[P, R]) -> Callable[P, R]:
    """Decorator that emails stack trace for unhandled exceptions."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(
                "Unhandled exception in %s: %s", func.__name__, exc, exc_info=True
            )
            _publish_trace(func.__name__, traceback.format_exc())
            raise

    return wrapper
