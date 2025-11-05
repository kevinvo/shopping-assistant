import inspect
import os
from dataclasses import dataclass, field
import boto3
import logging
from typing import Dict, Any, List, Optional, ClassVar, Type, TypeVar
from chalicelib.models.data_objects import ChatMessage
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_aws_region() -> Optional[str]:
    """Resolve the AWS region for boto3 clients/resources."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None


_REGION = _get_aws_region()


if _REGION:
    dynamodb = boto3.resource("dynamodb", region_name=_REGION)
else:
    dynamodb = boto3.resource("dynamodb")


CONNECTIONS_TABLE_NAME = os.environ.get(
    "CONNECTIONS_TABLE_NAME", "WebSocketConnectionsV2"
)

SESSIONS_TABLE_V2_NAME = os.environ.get("SESSIONS_TABLE_V2_NAME", "SessionsTableV2")

T = TypeVar("T", bound="DynamoDBStorageQueryMixin")


class DynamoDBStorageQueryMixin(ABC):
    @property
    @abstractmethod
    def dynamo_table_name(self) -> str:
        """Return the DynamoDB table name for this model"""
        pass

    @abstractmethod
    def to_item(self) -> Dict[str, Any]:
        """Convert this object to a DynamoDB item"""
        pass

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """Create an instance from a dictionary"""
        return cls(
            **{k: v for k, v in data.items() if k in inspect.signature(cls).parameters}
        )

    @classmethod
    def get_by_id(cls: Type[T], id: str) -> Optional[T]:
        """Get an item by its ID"""
        table = dynamodb.Table(cls.dynamo_table_name)  # type: ignore
        key_dict = {"id": id}
        response = table.get_item(Key=key_dict)
        item_dict = response.get("Item", {})
        return cls.from_dict(data=item_dict) if item_dict else None

    def save(self: T) -> T:
        """Save this item to DynamoDB"""
        table = dynamodb.Table(self.dynamo_table_name)  # type: ignore
        item_dict = self.to_item()
        table.put_item(Item=item_dict)
        return self

    @classmethod
    def delete_by_id(cls, id: str) -> None:
        """Delete an item by its ID"""
        table = dynamodb.Table(cls.dynamo_table_name)  # type: ignore
        key_dict = {"id": id}
        table.delete_item(Key=key_dict)

    def delete(self) -> None:
        """Delete this item from DynamoDB"""
        table = dynamodb.Table(self.dynamo_table_name)  # type: ignore
        key_dict = {"id": getattr(self, "id")}
        table.delete_item(Key=key_dict)


@dataclass
class SessionData:
    created_at: str
    last_active: str

    def to_item(self) -> Dict[str, Any]:
        return {
            "created_at": self.created_at,
            "last_active": self.last_active,
        }

    def __str__(self) -> str:
        return (
            f"SessionData(created_at={self.created_at}, last_active={self.last_active})"
        )


@dataclass
class ConnectionInfo(DynamoDBStorageQueryMixin):
    id: str
    ttl: int
    connected_at: str
    chat_history: List[ChatMessage] = field(default_factory=list)
    session_id: Optional[str] = None
    dynamo_table_name: ClassVar[str] = CONNECTIONS_TABLE_NAME

    def __post_init__(self):
        if isinstance(self.chat_history, list):
            processed_chat_history = []
            for msg in self.chat_history:
                if isinstance(msg, dict):
                    processed_chat_history.append(ChatMessage.from_dict(msg))
                elif isinstance(msg, ChatMessage):
                    processed_chat_history.append(msg)
            self.chat_history = processed_chat_history

    def to_item(self) -> Dict[str, Any]:
        item = {
            "id": self.id,
            "ttl": self.ttl,
            "connected_at": self.connected_at,
            "chat_history": [msg.to_dict() for msg in self.chat_history],
        }
        item["session_id"] = self.session_id if self.session_id else None
        return item

    def to_dict(self) -> Dict[str, Any]:
        return self.to_item()


@dataclass
class SessionInfo(DynamoDBStorageQueryMixin):
    id: str
    data: SessionData
    expiry_time: int
    dynamo_table_name: ClassVar[str] = SESSIONS_TABLE_V2_NAME

    def to_item(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "data": self.data.to_item(),
            "expiry_time": self.expiry_time,
        }

    @classmethod
    def get_by_session_id(cls, session_id: str) -> Optional["SessionInfo"]:
        return cls.get_by_id(id=session_id)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionInfo":
        session_data_dict = data.get("data", {})
        now = datetime.now().isoformat()

        if session_data_dict:
            session_data = SessionData(
                created_at=session_data_dict.get("created_at", now),
                last_active=session_data_dict.get("last_active", now),
            )
        else:
            session_data = SessionData(
                created_at=now,
                last_active=now,
            )

        return cls(
            id=data.get("id", ""),
            data=session_data,
            expiry_time=data.get("expiry_time", 0),
        )
