from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Union
from enum import Enum
import json
from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage


class MessageType(str, Enum):
    MESSAGE = "message"
    PROCESSING = "processing"
    ERROR = "error"


@dataclass
class ChatMessage:
    """Represents a chat message in the conversation history."""

    role: str
    content: str

    def __str__(self) -> str:
        """Return a string representation of the chat message."""
        return f"ChatMessage(role={self.role}, content={self.content})"

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary format."""
        return {"role": self.role, "content": self.content}

    def is_system(self) -> bool:
        """Return True if the message role is 'system'."""
        return self.role == "system"

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ChatMessage":
        """Create a ChatMessage from a dictionary."""
        return cls(role=data.get("role", ""), content=data.get("content", ""))

    def to_langchain_message(self) -> Union[HumanMessage, SystemMessage, AIMessage]:
        """Convert ChatMessage to appropriate LangChain message type."""
        if self.role == "system":
            return SystemMessage(content=self.content)
        elif self.role == "assistant":
            return AIMessage(content=self.content)
        elif self.role == "user":
            return HumanMessage(content=self.content)
        else:
            # Default to HumanMessage for unknown roles
            return HumanMessage(content=self.content)


@dataclass
class SearchResult:
    """Represents a search result from the vector database."""

    text: str
    metadata: Dict[str, Any]
    score: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {"text": self.text, "metadata": self.metadata, "score": self.score}

    def to_reranker_dict(self) -> Dict[str, Any]:
        """Convert to the format expected by LLMReranker."""
        return {
            "payload": {"text": self.text, "metadata": self.metadata},
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SearchResult":
        """Create a SearchResult from a dictionary."""
        return cls(
            text=data.get("text", ""),
            metadata=data.get("metadata", {}),
            score=data.get("score", 0.0),
        )

    @classmethod
    def from_reranker_dict(cls, data: Dict[str, Any]) -> "SearchResult":
        """Create a SearchResult from reranker dictionary format."""
        return cls(
            text=data.get("payload", {}).get("text", ""),
            metadata=data.get("payload", {}).get("metadata", {}),
            score=data.get("score", 0.0),
        )


@dataclass
class RetrievalMetricsDocument:
    """Represents a document for retrieval metrics computation."""

    doc_id: str
    text: str
    score: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for metrics computation."""
        return {
            "doc_id": self.doc_id,
            "text": self.text,
            "score": self.score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_search_result(
        cls, search_result: "SearchResult", doc_id: str
    ) -> "RetrievalMetricsDocument":
        """Create from a SearchResult with a computed doc_id."""
        return cls(
            doc_id=doc_id,
            text=search_result.text,
            score=search_result.score,
            metadata=search_result.metadata,
        )


@dataclass
class RerankerJudgment:
    """Represents a reranker's relevance judgment for a document."""

    doc_id: str
    relevance_score: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for serialization."""
        return {
            "doc_id": self.doc_id,
            "relevance_score": self.relevance_score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RerankerJudgment":
        """Create from dictionary."""
        return cls(
            doc_id=data["doc_id"],
            relevance_score=float(data["relevance_score"]),
        )


@dataclass
class MessagePayload:
    """Data class for WebSocket message payloads sent to SQS."""

    connection_id: str
    domain_name: str
    stage: str
    message: str
    request_id: str
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict) -> "MessagePayload":
        """Create a MessagePayload instance from a dictionary."""
        return cls(
            connection_id=data.get("connection_id", ""),
            domain_name=data.get("domain_name", ""),
            stage=data.get("stage", ""),
            message=data.get("message", ""),
            request_id=data.get("request_id", ""),
            timestamp=data.get("timestamp", ""),
        )

    def to_dict(self) -> dict:
        """Convert the MessagePayload to a dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert the MessagePayload to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def create(
        cls,
        *,
        connection_id: str,
        domain_name: str,
        stage: str,
        message: str,
        request_id: str,
    ) -> "MessagePayload":
        """Create a new MessagePayload with the current timestamp."""
        return cls(
            connection_id=connection_id,
            domain_name=domain_name,
            stage=stage,
            message=message,
            request_id=request_id,
            timestamp=datetime.now().isoformat(),
        )


@dataclass
class ResponsePayload:
    """Data class for WebSocket response payloads sent to clients."""

    type: MessageType
    content: str
    request_id: str
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict) -> "ResponsePayload":
        """Create a ResponsePayload instance from a dictionary."""
        return cls(
            type=data.get("type", MessageType.MESSAGE),
            content=data.get("content", ""),
            request_id=data.get("request_id", ""),
            timestamp=data.get("timestamp", ""),
        )

    def to_dict(self) -> dict:
        """Convert the ResponsePayload to a dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert the ResponsePayload to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def create_processing(
        cls, *, request_id: str, content: str = "Your request is being processed..."
    ) -> "ResponsePayload":
        """Create a new processing response with the current timestamp."""
        return cls(
            type=MessageType.PROCESSING,
            content=content,
            request_id=request_id,
            timestamp=datetime.now().isoformat(),
        )

    @classmethod
    def create_message(cls, *, request_id: str, content: str) -> "ResponsePayload":
        """Create a new message response with the current timestamp."""
        return cls(
            type=MessageType.MESSAGE,
            content=content,
            request_id=request_id,
            timestamp=datetime.now().isoformat(),
        )

    @classmethod
    def create_error(
        cls,
        *,
        request_id: str,
        content: str = "Sorry, there was an error processing your request.",
    ) -> "ResponsePayload":
        """Create a new error response with the current timestamp."""
        return cls(
            type=MessageType.ERROR,
            content=content,
            request_id=request_id,
            timestamp=datetime.now().isoformat(),
        )


@dataclass(frozen=True)
class EvaluationMessage:
    """Represents the payload sent to the evaluation SQS queue."""

    query: str
    response: str
    session_id: str
    request_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the evaluation message to a dictionary."""
        return {
            "query": self.query,
            "response": self.response,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvaluationMessage":
        """Deserialize an evaluation message from a dictionary."""
        return cls(
            query=data.get("query", ""),
            response=data.get("response", ""),
            session_id=data.get("session_id", ""),
            request_id=data.get("request_id", ""),
            metadata=data.get("metadata", {}) or {},
            timestamp=data.get("timestamp") or datetime.utcnow().isoformat(),
        )
