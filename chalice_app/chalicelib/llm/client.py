import json
import logging
from typing import List, Generator
from abc import ABC, abstractmethod
from enum import Enum
from openai import OpenAI
from chalicelib.core.config import config as app_config
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.models.data_objects import ChatMessage
from chalicelib.prompts import (
    CONTEXT_AWARE_PROMPT_REWRITING,
    PROMPT_REWRITE_INSTRUCTION,
    HYDE_GENERATION_PROMPT,
    HYDE_SYSTEM_PROMPT,
)
from langsmith import traceable
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from dataclasses import dataclass
from typing import Dict, Any


logger = logging.getLogger()
logger.setLevel(logging.INFO)


_models_rebuilt = False


def _ensure_models_rebuilt():
    """Rebuild LangChain models once per process (forward-ref workaround)."""
    global _models_rebuilt
    if _models_rebuilt:
        return
    try:
        ChatOpenAI.model_rebuild()
    except Exception as e:
        logger.warning(f"Model rebuild warning: {e}")
    # Set the flag even on failure so we don't retry on every call.
    _models_rebuilt = True


@dataclass
class LLMRequestParams:
    model: str
    messages: List[Dict[str, str]]
    system: str
    temperature: float = 0.7
    max_tokens: int = 1000
    top_p: float = 0.95

    def to_dict(self) -> Dict[str, Any]:
        """Convert the dataclass to a dictionary for API requests"""
        return {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "system": self.system,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMRequestParams":
        """Create an instance from a dictionary"""
        return cls(
            model=data["model"],
            messages=data["messages"],
            system=data["system"],
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 1000),
            top_p=data.get("top_p", 0.95),
        )


@dataclass
class DeepSeekRequestParams:
    model: str
    messages: List[Dict[str, str]]
    temperature: float = 0.7
    max_tokens: int = 2000  # Increased for better capabilities
    top_p: float = 0.95
    frequency_penalty: float = 0
    presence_penalty: float = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert the dataclass to a dictionary for API requests"""
        return {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeepSeekRequestParams":
        """Create an instance from a dictionary"""
        return cls(
            model=data["model"],
            messages=data["messages"],
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 1000),
            top_p=data.get("top_p", 0.95),
            frequency_penalty=data.get("frequency_penalty", 0),
            presence_penalty=data.get("presence_penalty", 0),
        )


class LLMProvider(Enum):
    """Enum for supported LLM providers"""

    DEEPSEEK = "deepseek"


class BaseLLM(ABC):
    @abstractmethod
    def chat(self, messages: List[ChatMessage], **kwargs) -> str:
        pass

    def stream_chat(
        self, messages: List[ChatMessage], **kwargs
    ) -> Generator[str, None, None]:
        full_response = self.chat(messages, **kwargs)
        yield full_response

    @staticmethod
    def _trim_history(
        message_history: List[ChatMessage], keep_recent: int = 6
    ) -> List[ChatMessage]:
        """Keep the system message + the last N user/assistant turns."""
        copy = message_history.copy()
        if len(copy) > keep_recent + 1:
            copy = [copy[0]] + copy[-keep_recent:]
        return copy

    @traceable(name="rewrite_query")
    @measure_execution_time
    def rewrite_query(
        self, last_message_content: str, message_history: List[ChatMessage]
    ) -> str:
        """Rewrite the query using conversation context.

        Resolves pronouns / topic-elision against the recent history. Output
        is JSON-mode so we can drop max_tokens aggressively.
        """
        logger.info(f"Rewriting prompt: {last_message_content}")
        messages = self._trim_history(message_history)
        if messages:
            messages[0] = ChatMessage(
                role="system",
                content=(
                    CONTEXT_AWARE_PROMPT_REWRITING
                    + "\n\nFocus only on the most recent and relevant context. "
                    + "If the user is asking about a new topic, completely "
                    + "ignore previous topics."
                ),
            )
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    PROMPT_REWRITE_INSTRUCTION.format(query=last_message_content)
                    + '\n\nReturn JSON: {"rewritten_query": "..."}'
                ),
            )
        )
        response = self.chat(
            messages=messages,
            temperature=0.2,
            max_tokens=120,
            json_mode=True,
        )
        rewritten = json.loads(response).get("rewritten_query") or last_message_content
        logger.info(f"Rewritten query: {rewritten}")
        return rewritten

    @traceable(name="generate_hyde")
    @measure_execution_time
    def generate_hyde(
        self, last_message_content: str, message_history: List[ChatMessage]
    ) -> str:
        """Generate a hypothetical document embedding (HyDE) seed string.

        Uses the conversation history directly (NOT the rewritten query) so
        this call can run in parallel with rewrite. Output is intentionally
        short (~30 tokens) -- a keyword-rich phrase outperforms a full
        paragraph for embedding-space retrieval and generates faster.
        """
        logger.info(f"Generating HyDE for prompt: {last_message_content}")
        messages = self._trim_history(message_history)
        if messages:
            messages[0] = ChatMessage(role="system", content=HYDE_SYSTEM_PROMPT)
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    HYDE_GENERATION_PROMPT.format(query=last_message_content)
                    + "\n\nResolve any pronouns or topic ellipsis against the "
                    + "conversation above. Output a concise comma-separated list "
                    + "of product types and key features (~20 tokens), not a "
                    + 'sentence. Return JSON: {"hyde_response": "..."}'
                ),
            )
        )
        response = self.chat(
            messages=messages,
            temperature=0.2,
            max_tokens=80,
            json_mode=True,
        )
        hyde = json.loads(response).get("hyde_response") or last_message_content
        logger.info(f"HyDE response: {hyde}")
        return hyde


class DeepSeekClient(BaseLLM):
    def __init__(self):
        # Reuse the module-level AppConfig singleton; a fresh AppConfig()
        # here would re-fetch from Secrets Manager on every chat request
        # (~3-5s on cold start, ~200ms warm).
        self.config = app_config
        self.client = OpenAI(
            api_key=self.config.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=self._build_headers(),
        )
        # `:nitro` tells OpenRouter to route each call to whichever upstream
        # provider is currently fastest (DeepInfra, Fireworks, etc.) instead
        # of cheapest. Trades ~2-3x per-token cost for much lower TTFT
        # variance -- the 0.7s vs 7s swings we saw in production logs were
        # the default routing landing on a slow provider. Falls back to the
        # default pool automatically if `:nitro` is rate-limited.
        self.model = "deepseek/deepseek-chat:nitro"
        # Cache ChatOpenAI by the kwargs that vary across calls. Building a
        # fresh ChatOpenAI per call discards the underlying httpx pool to
        # OpenRouter and adds ~100-200ms of TLS setup per invocation. In
        # practice this dict ends up with ~2 entries (rewrite/JSON + final
        # stream).
        self._langchain_cache: Dict[tuple, ChatOpenAI] = {}

    def _build_headers(self) -> Dict[str, str]:
        """Build headers for OpenRouter requests, including DeepSeek API key if available."""
        headers = {
            "HTTP-Referer": "https://github.com/your-org/shopping-assistant-agent",
            "X-Title": "Shopping Assistant Agent",
        }
        # Add DeepSeek provider key if available (OpenRouter will use it to accumulate rate limits)
        try:
            deepseek_key = self.config.deepseek_api_key
            if deepseek_key:
                headers["X-DeepSeek-Key"] = deepseek_key
        except ValueError:
            # DeepSeek key not configured, that's okay - OpenRouter will use shared rate limits
            pass
        return headers

    def _get_langchain_client(
        self,
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        json_mode: bool,
    ) -> ChatOpenAI:
        key = (temperature, top_p, max_tokens, json_mode)
        client = self._langchain_cache.get(key)
        if client is not None:
            return client
        _ensure_models_rebuilt()
        model_kwargs: Dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        client = ChatOpenAI(
            model=self.model,
            api_key=SecretStr(self.config.openrouter_api_key),
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,  # type: ignore[arg-type]
            model_kwargs=model_kwargs,
            default_headers=self._build_headers(),
        )
        self._langchain_cache[key] = client
        return client

    @measure_execution_time
    @traceable(name="deepseek_chat")
    def chat(self, messages: List[ChatMessage], **kwargs) -> str:
        try:
            langchain_messages = [m.to_langchain_message() for m in messages]
            langchain_client = self._get_langchain_client(
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.95),
                max_tokens=kwargs.get("max_tokens", 2000),
                json_mode=kwargs.get("json_mode", False),
            )
            response = langchain_client.invoke(langchain_messages)
            return str(response.content) if response.content else ""
        except Exception as e:
            logger.error(f"Error in DeepSeek chat: {e}")
            raise

    @measure_execution_time
    @traceable(name="deepseek_stream_chat")
    def stream_chat(
        self, messages: List[ChatMessage], **kwargs
    ) -> Generator[str, None, None]:
        try:
            langchain_messages = [m.to_langchain_message() for m in messages]
            langchain_client = self._get_langchain_client(
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.95),
                max_tokens=kwargs.get("max_tokens", 2000),
                json_mode=kwargs.get("json_mode", False),
            )
            for chunk in langchain_client.stream(langchain_messages):
                if chunk.content:
                    yield chunk.content
        except Exception as e:
            logger.error(f"Error in DeepSeek stream_chat: {e}")
            raise


class LLMFactory:
    @staticmethod
    def create_llm(provider: LLMProvider = LLMProvider.DEEPSEEK) -> BaseLLM:
        if provider == LLMProvider.DEEPSEEK:
            return DeepSeekClient()
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")
