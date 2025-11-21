import json
import logging
from typing import List, Generator
from abc import ABC, abstractmethod
from enum import Enum
from openai import OpenAI
from chalicelib.core.config import AppConfig
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.models.data_objects import ChatMessage, RewriteAndHyDEResult
from langsmith import traceable
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from dataclasses import dataclass
from typing import Dict, Any


logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _ensure_models_rebuilt():
    """Ensure all LangChain models are properly rebuilt with all dependencies."""
    try:
        # Try to rebuild models - this is still needed in current LangChain versions
        # due to complex forward reference issues in the LangChain codebase
        ChatOpenAI.model_rebuild()
    except Exception as e:
        logger.warning(f"Model rebuild warning: {e}")
        # Continue anyway - models might already be built


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


CONTEXT_AWARE_PROMPT_REWRITING = """
Analyze the conversation history and rewrite the most recent user prompt/query, with these guidelines:

1. Identify if the latest query represents a topic change or new conversation direction.
   - If it contains new keywords, entities, or question types not present in recent exchanges, treat it as a topic change
   - Look for explicit signals like "let's talk about something else" or completely unrelated questions
   - Even questions that relate to previous topics but focus on a new aspect should be treated as topic shifts
   - IMPORTANT: If the query contains specific product names, ingredients, or concepts not mentioned in recent exchanges, treat it as a new topic

2. For topic changes:
   - Treat the query as a fresh conversation starting point - DO NOT carry over previous context as the main focus
   - Expand the query to be fully self-contained without relying on previous topics
   - If the query uses ambiguous references (like "it", "this", "that") but appears to be a new topic, interpret these references as part of the new topic only
   - Signal the topic change by starting the rewritten query with clear, specific language about the new subject
   - Prior topics should only be mentioned if directly relevant to understanding the new question
   - NEVER introduce information about previous topics unless explicitly requested

3. For continued conversations:
   - Ensure the rewritten query is coherent, precise, and contextually relevant
   - Expand pronouns and unclear references based on the conversation history
   - Maintain the user's original intent while adding clarity
   - Focus on the most recent topics and questions rather than summarizing the entire conversation
   - Prioritize the specific question being asked over general context from earlier exchanges

4. In all cases:
   - Preserve the user's core question or request as the primary focus
   - Do not introduce information not implied or requested by the user
   - Focus on making the query self-contained and clear
   - When a user asks a direct follow-up question, prioritize that specific question rather than recapping earlier conversation
   - Avoid unnecessary summarization of previous exchanges unless explicitly requested
   - IMPORTANT: When a user introduces a new item or topic (like "What do you think about X?"), make that new topic the primary focus
   - NEVER rewrite a query to focus on a previous topic when the user is clearly asking about something new

The goal is to detect topic shifts decisively and prevent previous conversation context from contaminating new topics while maintaining coherence for continued conversations.
""".strip()

PROMPT_REWRITE_INSTRUCTION = """
Please rewrite this prompt to be more context-aware. If this prompt introduces a new topic or question, 
make sure your rewritten version focuses primarily on this new topic rather than previous conversation topics. 
IMPORTANT: If this query contains specific product names, ingredients, or concepts not mentioned in recent exchanges, 
treat it as a new topic and focus exclusively on that. Return ONLY the rewritten prompt with no explanations or additional text: {query}
""".strip()

HYDE_GENERATION_PROMPT = """
Given this shopping question, write a brief, consistent hypothetical answer that focuses on key product recommendations and main points.

IMPORTANT: Be consistent and deterministic. For the same question, generate similar answers focusing on:
1. The most relevant product categories/types mentioned in the question
2. Key features or attributes that would be important for this type of product
3. Practical considerations or use cases

Keep it concise (2-3 sentences max). Focus on what would be most relevant for searching Reddit discussions.

Question: {query}

Hypothetical Answer:
""".strip()

HYDE_SYSTEM_PROMPT = """
You are a shopping assistant generating hypothetical answers for search purposes. 
Generate concise, consistent, and deterministic hypothetical answers to shopping questions.
Write as if summarizing key points from a Reddit discussion - focus on product types, key features, and practical advice.
Be brief (2-3 sentences), informative, and consistent - for the same question, generate similar answers.
Focus on terms and concepts that would help find relevant Reddit discussions.
""".strip()


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

    @traceable(name="rewrite_and_generate_hyde")
    @measure_execution_time
    def rewrite_and_generate_hyde(
        self, last_message_content: str, message_history: List[ChatMessage]
    ) -> RewriteAndHyDEResult:
        """
        Rewrite the query with context and generate HyDE in a single LLM call.
        Reuses existing rewrite and HyDE prompts.
        """
        logger.info(f"Combined rewrite and HyDE for prompt: {last_message_content}")

        message_history_copy = message_history.copy()

        recent_messages_count = 6
        if len(message_history_copy) > recent_messages_count + 1:
            message_history_copy = [message_history_copy[0]] + message_history_copy[
                -recent_messages_count:
            ]

        combined_system_prompt = (
            CONTEXT_AWARE_PROMPT_REWRITING
            + "\n\nFocus only on the most recent and relevant context. "
            + "If the user is asking about a new topic, completely ignore previous topics.\n\n"
            + HYDE_SYSTEM_PROMPT
        )

        combined_user_prompt = f"""
    Perform these two tasks in order:

    TASK 1 - Rewrite the query:
    {PROMPT_REWRITE_INSTRUCTION.format(query=last_message_content)}

    TASK 2 - Generate HyDE (Hypothetical Document Embedding):
    After rewriting the query, use the rewritten query to generate a brief, consistent hypothetical answer.
    IMPORTANT: Keep the HyDE response concise (2-3 sentences), focused on product types and key features mentioned in the rewritten query.
    Be deterministic - similar queries should generate similar HyDE responses.
    
    {HYDE_GENERATION_PROMPT.format(query="[USE YOUR REWRITTEN QUERY FROM TASK 1]")}

    Return a JSON object with both results:
    {{
      "rewritten_query": "your rewritten query here",
      "hyde_response": "your brief hypothetical answer here (2-3 sentences max)"
    }}
    """

        if len(message_history_copy) > 0:
            message_history_copy[0] = ChatMessage(
                role="system",
                content=combined_system_prompt,
            )

        message_history_copy.append(
            ChatMessage(role="user", content=combined_user_prompt)
        )

        response = self.chat(
            messages=message_history_copy,
            temperature=0.2,  # Lower temperature for more consistency
            max_tokens=500,  # Reduced tokens to encourage brevity and consistency
            json_mode=True,
        )

        result = json.loads(response)

        logger.info(f"Rewritten query: {result['rewritten_query']}")
        logger.info(f"HyDE response: {result['hyde_response']}")

        return RewriteAndHyDEResult(
            rewritten_query=result["rewritten_query"],
            hyde_response=result["hyde_response"],
        )


class DeepSeekClient(BaseLLM):
    def __init__(self):
        self.config = AppConfig()
        self.client = OpenAI(
            api_key=self.config.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=self._build_headers(),
        )
        self.model = "deepseek/deepseek-chat"

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

    @measure_execution_time
    @traceable(name="deepseek_chat")
    def chat(self, messages: List[ChatMessage], **kwargs) -> str:
        try:
            # Convert ChatMessage objects to LangChain messages for LangSmith tracking
            langchain_messages = [m.to_langchain_message() for m in messages]

            # Ensure model is rebuilt before creating client
            _ensure_models_rebuilt()

            # Prepare model_kwargs for JSON mode if requested
            model_kwargs = {}
            if kwargs.get("json_mode", False):
                model_kwargs["response_format"] = {"type": "json_object"}

            # Use LangChain ChatOpenAI for proper LangSmith integration
            langchain_client = ChatOpenAI(
                model=self.model,
                api_key=SecretStr(self.config.openrouter_api_key),
                base_url="https://openrouter.ai/api/v1",
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.95),
                max_tokens=kwargs.get("max_tokens", 2000),  # type: ignore[arg-type]
                model_kwargs=model_kwargs,
                default_headers=self._build_headers(),
            )

            # Use LangChain client which will automatically track with LangSmith
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
            _ensure_models_rebuilt()

            model_kwargs = {}
            if kwargs.get("json_mode", False):
                model_kwargs["response_format"] = {"type": "json_object"}

            langchain_client = ChatOpenAI(
                model=self.model,
                api_key=SecretStr(self.config.openrouter_api_key),
                base_url="https://openrouter.ai/api/v1",
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.95),
                max_tokens=kwargs.get("max_tokens", 2000),  # type: ignore[arg-type]
                model_kwargs=model_kwargs,
                default_headers=self._build_headers(),
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
