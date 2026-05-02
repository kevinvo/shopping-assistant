import inspect
import os
from dataclasses import dataclass, field
import boto3
import logging
from typing import Dict, Any, List, Optional, ClassVar, Type, TypeVar
from chalicelib.models.data_objects import ChatMessage
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

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

CONVERSATION_HISTORIES_TABLE_NAME = os.environ.get(
    "CONVERSATION_HISTORIES_TABLE_NAME", "ConversationHistoriesV1"
)

CONVERSATION_HISTORY_TTL_DAYS = 30

SUGGESTED_PROMPTS_TABLE_NAME = os.environ.get(
    "SUGGESTED_PROMPTS_TABLE_NAME", "SuggestedPromptsV1"
)

# Single global record id used by SuggestedPrompts.
SUGGESTED_PROMPTS_GLOBAL_ID = "global"

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


def conversation_history_id(session_id: str, conversation_id: str) -> str:
    """Composite key for ConversationHistory."""
    return f"{session_id}#{conversation_id}"


@dataclass
class ConversationHistory(DynamoDBStorageQueryMixin):
    """Per-conversation chat history keyed by <session_id>#<conversation_id>."""

    id: str
    session_id: str
    conversation_id: str
    last_active: str
    expiry_time: int
    messages: List[ChatMessage] = field(default_factory=list)
    dynamo_table_name: ClassVar[str] = CONVERSATION_HISTORIES_TABLE_NAME

    def __post_init__(self):
        if isinstance(self.messages, list):
            processed: List[ChatMessage] = []
            for msg in self.messages:
                if isinstance(msg, dict):
                    processed.append(ChatMessage.from_dict(msg))
                elif isinstance(msg, ChatMessage):
                    processed.append(msg)
            self.messages = processed

    def to_item(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "last_active": self.last_active,
            "expiry_time": self.expiry_time,
            "messages": [m.to_dict() for m in self.messages],
        }

    def bump_expiry(self) -> None:
        """Refresh last_active to now and push expiry_time CONVERSATION_HISTORY_TTL_DAYS forward."""
        now = datetime.now()
        self.last_active = now.isoformat()
        self.expiry_time = int(
            (now + timedelta(days=CONVERSATION_HISTORY_TTL_DAYS)).timestamp()
        )

    @classmethod
    def get(
        cls, session_id: str, conversation_id: str
    ) -> Optional["ConversationHistory"]:
        return cls.get_by_id(id=conversation_history_id(session_id, conversation_id))

    @classmethod
    def new(cls, session_id: str, conversation_id: str) -> "ConversationHistory":
        now = datetime.now()
        return cls(
            id=conversation_history_id(session_id, conversation_id),
            session_id=session_id,
            conversation_id=conversation_id,
            last_active=now.isoformat(),
            expiry_time=int(
                (now + timedelta(days=CONVERSATION_HISTORY_TTL_DAYS)).timestamp()
            ),
            messages=[],
        )


@dataclass
class SuggestedPrompts(DynamoDBStorageQueryMixin):
    """Cached starter prompts for the empty-state UI.

    Persisted as a single record with id="global". The suggested_prompts cron
    overwrites this every 4 days using LLM output grounded in the indexed
    Reddit content, and the public REST endpoint reads it on demand.
    """

    id: str
    prompts: List[str]
    generated_at: str
    sources_used: List[str] = field(default_factory=list)
    dynamo_table_name: ClassVar[str] = SUGGESTED_PROMPTS_TABLE_NAME

    def to_item(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prompts": list(self.prompts),
            "generated_at": self.generated_at,
            "sources_used": list(self.sources_used),
        }

    @classmethod
    def load(cls) -> Optional["SuggestedPrompts"]:
        return cls.get_by_id(id=SUGGESTED_PROMPTS_GLOBAL_ID)

    @classmethod
    def new(
        cls, prompts: List[str], sources_used: Optional[List[str]] = None
    ) -> "SuggestedPrompts":
        return cls(
            id=SUGGESTED_PROMPTS_GLOBAL_ID,
            prompts=list(prompts),
            generated_at=datetime.now().isoformat(),
            sources_used=list(sources_used or []),
        )
