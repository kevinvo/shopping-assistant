"""Shared logging utilities."""

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class LogExtra:
    """Structured data for logger extra fields."""

    connection_id: Optional[str] = None
    request_id: Optional[str] = None
    domain_name: Optional[str] = None
    stage: Optional[str] = None
    message_type: Optional[str] = None
    queue_url: Optional[str] = None
    sqs_message_id: Optional[str] = None
    chat_history_length: Optional[int] = None
    updated_chat_history_length: Optional[int] = None
    evaluation_metadata_keys: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return non-null fields for use with logger extra."""
        return {key: value for key, value in asdict(self).items() if value is not None}
