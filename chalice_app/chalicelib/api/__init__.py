"""Aggregated API entrypoints for Chalice routes."""

from .rest import register_rest_routes
from .websocket import (
    handle_websocket_connect,
    handle_websocket_disconnect,
    handle_websocket_message,
)

__all__ = [
    "register_rest_routes",
    "handle_websocket_connect",
    "handle_websocket_disconnect",
    "handle_websocket_message",
]
