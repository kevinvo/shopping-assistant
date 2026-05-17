import json
import logging
from typing import List, Generator, Optional
from abc import ABC, abstractmethod
from enum import Enum
from openai import OpenAI
from chalicelib.core.config import config as app_config
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.models.data_objects import ChatMessage
from chalicelib.prompts import (
    get_context_aware_rewrite,
    get_hyde_system,
    get_hyde_user,
    get_rewrite_user_instruction,
)
from langsmith import traceable, get_current_run_tree
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from dataclasses import dataclass
from typing import Dict, Any


logger = logging.getLogger()
logger.setLevel(logging.INFO)


_models_rebuilt = False


def _tag_run(**metadata) -> None:
    """Attach metadata to the current LangSmith run if one is active.

    No-op outside a @traceable context (tests, scripts). Caught errors
    are warned-and-swallowed -- this is best-effort observability, not
    load-bearing.
    """
    try:
        run = get_current_run_tree()
        if run is not None:
            run.add_metadata(metadata)
    except Exception as e:  # pragma: no cover - logging only
        logger.warning("Failed to tag run metadata: %s", e)


# Tail of conversation history fed into rewrite/HyDE. Past this window the
# established topic dominates the few-shot examples and small-model
# topic-shift detection regresses (gpt-4o-mini "playing it safe" by
# inheriting prior context). 4 messages = 2 prior exchanges — enough for
# pronoun and topic-ellipsis resolution, short enough that drift from
# older turns can't outweigh the new query.
_REWRITE_HISTORY_TAIL = 4
_HYDE_HISTORY_TAIL = 4


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
    # Subclasses may override these to route specific short LLM calls to
    # cheaper/faster models without affecting the main answer stream. Both
    # default to None, which means "use whatever the default model is".
    rewrite_model: Optional[str] = None
    hyde_model: Optional[str] = None

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

    def _build_chat_messages(
        self,
        message_history: List[ChatMessage],
        system_prompt: str,
        user_prompt: str,
    ) -> List[ChatMessage]:
        messages = self._trim_history(message_history)
        if messages:
            messages[0] = ChatMessage(role="system", content=system_prompt)
        messages.append(ChatMessage(role="user", content=user_prompt))
        return messages

    @traceable(name="rewrite_query")
    @measure_execution_time
    def rewrite_query(
        self, last_message_content: str, message_history: List[ChatMessage]
    ) -> str:
        """Rewrite the query using conversation context.

        Resolves pronouns / topic-elision against the recent history. JSON
        mode so we can keep max_tokens tight.
        """
        sys_text, sys_version = get_context_aware_rewrite()
        user_template, user_version = get_rewrite_user_instruction()
        _tag_run(rewrite_system_version=sys_version, rewrite_user_version=user_version)

        messages = self._build_chat_messages(
            message_history[-_REWRITE_HISTORY_TAIL:],
            system_prompt=sys_text.format(),
            user_prompt=user_template.format(query=last_message_content),
        )
        response = self.chat(
            messages=messages,
            temperature=0.2,
            max_tokens=120,
            json_mode=True,
            model=self.rewrite_model,
        )
        return json.loads(response).get("rewritten_query") or last_message_content

    @traceable(name="generate_hyde")
    @measure_execution_time
    def generate_hyde(
        self, last_message_content: str, message_history: List[ChatMessage]
    ) -> Optional[str]:
        """Generate a hypothetical document embedding (HyDE) seed string.

        Uses the conversation history directly (NOT the rewritten query) so
        this call can run in parallel with rewrite. Output is intentionally
        short (~20 tokens) -- a keyword-rich phrase outperforms a paragraph
        for embedding-space retrieval and generates faster. Skipping JSON
        mode here trims ~100-300 ms of TTFT on the short output.

        Returns None when the model produces nothing usable, signaling the
        caller to skip the HyDE-driven search rather than embedding the raw
        query a second time and burning ~1.3 s of Qdrant round-trip on
        results we'd just dedupe away in _combine_search_results.
        """
        sys_text, sys_version = get_hyde_system()
        user_template, user_version = get_hyde_user()
        _tag_run(hyde_system_version=sys_version, hyde_user_version=user_version)

        messages = self._build_chat_messages(
            message_history[-_HYDE_HISTORY_TAIL:],
            system_prompt=sys_text.format(),
            user_prompt=user_template.format(query=last_message_content),
        )
        response = self.chat(
            messages=messages,
            temperature=0.2,
            max_tokens=40,
        )
        hyde = response.strip()
        if not hyde:
            return None
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
        # `:nitro` routes through whichever upstream provider is currently
        # fastest (DeepInfra, Fireworks, etc.) at ~2-3x per-token cost. We
        # use it for the short latency-sensitive calls (rewrite + HyDE) where
        # the variance was costing us 4-7s of TTFT, but NOT for the final
        # answer stream -- stream_model emits 20-50x more tokens per call,
        # so paying nitro's premium there would dominate the LLM bill for
        # marginal user-perceived benefit (the user is already reading by
        # the time the slow tokens arrive).
        self.model = "deepseek/deepseek-chat:nitro"
        # User preference: prioritize speed over the ~2-3x token-cost premium
        # nitro routing imposes. Compresses the TTFT long tail we saw at p95
        # (~11s outlier in the 20-query probe) by routing the answer stream
        # to whichever upstream DeepSeek provider is fastest that minute.
        self.stream_model = "deepseek/deepseek-chat:nitro"
        # Short structured rewrite: a small, fast OpenAI model is roughly
        # 2-3x faster TTFT than DeepSeek for this output shape, supports
        # JSON mode reliably, and costs pennies per month at our volume.
        # Keeps typo correction / query normalization that the user's
        # spell-check pushback flagged we'd lose if we skipped rewrite.
        self.rewrite_model = "openai/gpt-4o-mini"
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
        model: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
        json_mode: bool,
    ) -> ChatOpenAI:
        key = (model, temperature, top_p, max_tokens, json_mode)
        client = self._langchain_cache.get(key)
        if client is not None:
            return client
        _ensure_models_rebuilt()
        model_kwargs: Dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        client = ChatOpenAI(
            model=model,
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
                model=kwargs.get("model") or self.model,
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
                model=self.stream_model,
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
