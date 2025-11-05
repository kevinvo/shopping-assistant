import logging
from typing import List
from abc import ABC, abstractmethod
from enum import Enum
from openai import OpenAI
import anthropic
from chalicelib.config import AppConfig
from chalicelib.performance import measure_execution_time
from chalicelib.data_objects import ChatMessage
from langsmith import traceable
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
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
        ChatAnthropic.model_rebuild()
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
Given this shopping question, write a brief hypothetical answer with key product recommendations and main points.
Focus on specific products, features, and practical advice that would be in a Reddit discussion.

Question: {query}

Hypothetical Answer:
""".strip()

HYDE_SYSTEM_PROMPT = """
You are a shopping assistant. Generate concise hypothetical answers to shopping questions.
Write as if from a Reddit discussion with product recommendations, key features, and practical advice.
Be brief but informative - focus on the most important points only.
""".strip()


class LLMProvider(Enum):
    """Enum for supported LLM providers"""

    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"


class BaseLLM(ABC):
    @abstractmethod
    def chat(self, messages: List[ChatMessage], **kwargs) -> str:
        pass

    @traceable(name="rewrite_prompt")
    def rewrite_prompt(
        self, last_message_content: str, message_history: List[ChatMessage]
    ) -> str:
        logger.info(f"Original prompt: {last_message_content}")

        # Create a copy of message_history to avoid modifying the original
        message_history_copy = message_history.copy()

        # Keep system prompt (typically first message) and last 4 messages
        recent_messages_count = 6
        if len(message_history_copy) > recent_messages_count + 1:
            # Keep the first message (system prompt) and the last 4 messages
            message_history_copy = [message_history_copy[0]] + message_history_copy[
                -recent_messages_count:
            ]

        # Add explicit instruction to detect topic changes
        rewrite_request = ChatMessage(
            role="user",
            content=PROMPT_REWRITE_INSTRUCTION.format(query=last_message_content),
        )

        # Replace the system prompt with context-aware rewriting instructions
        if len(message_history_copy) > 0:
            system_content = (
                CONTEXT_AWARE_PROMPT_REWRITING
                + "\nFocus only on the most recent and relevant context. "
                + "If the user is asking about a new topic, completely ignore previous topics."
            )
            message_history_copy[0] = ChatMessage(
                role="system",
                content=system_content,
            )

        message_history_copy.append(rewrite_request)
        rewritten_prompt = self.chat(
            messages=message_history_copy, temperature=0.3, max_tokens=500
        )

        logger.info(f"Rewritten prompt: {rewritten_prompt}")
        return rewritten_prompt

    @traceable(name="generate_hyde")
    def generate_hyde(self, query: str) -> str:
        logger.info(f"Generating HyDE for query: {query}")

        messages = [
            ChatMessage(role="system", content=HYDE_SYSTEM_PROMPT),
            ChatMessage(
                role="user", content=HYDE_GENERATION_PROMPT.format(query=query)
            ),
        ]
        return self.chat(messages=messages, temperature=0.5, max_tokens=200)


class DeepSeekClient(BaseLLM):
    def __init__(self):
        self.config = AppConfig()
        self.client = OpenAI(
            api_key=self.config.deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
        )
        self.model = "deepseek-chat"

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
                api_key=SecretStr(self.config.deepseek_api_key),
                base_url="https://api.deepseek.com/v1",
                temperature=kwargs.get("temperature", 0.7),
                top_p=kwargs.get("top_p", 0.95),
                model_kwargs=model_kwargs,
            )

            # Use LangChain client which will automatically track with LangSmith
            response = langchain_client.invoke(langchain_messages)
            return str(response.content) if response.content else ""
        except Exception as e:
            logger.error(f"Error in DeepSeek chat: {e}")
            raise


class AnthropicClient(BaseLLM):
    def __init__(self):
        self.config = AppConfig()
        self.client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        self.model = "claude-3-7-sonnet-20250219"

    @measure_execution_time
    @traceable(name="anthropic_chat")
    def chat(self, messages: List[ChatMessage], **kwargs) -> str:
        try:
            # Convert ChatMessage objects to LangChain messages for LangSmith tracking
            langchain_messages = [m.to_langchain_message() for m in messages]

            # Ensure model is rebuilt before creating client
            _ensure_models_rebuilt()

            # For JSON mode with Anthropic, add a system message reminder
            # (Anthropic doesn't have response_format like OpenAI)
            if kwargs.get("json_mode", False):
                # Add a strong reminder at the end for JSON-only output
                json_reminder = HumanMessage(
                    content="Remember: Respond with ONLY valid JSON, no markdown formatting, no explanations."
                )
                langchain_messages.append(json_reminder)

            # Use LangChain ChatAnthropic for proper LangSmith integration
            langchain_client = ChatAnthropic(
                model_name=self.model,
                api_key=SecretStr(self.config.anthropic_api_key),
                temperature=kwargs.get("temperature", 0.7),
                max_tokens_to_sample=kwargs.get("max_tokens", 1000),
                timeout=60,
                stop=None,
            )

            # Use LangChain client which will automatically track with LangSmith
            response = langchain_client.invoke(langchain_messages)
            return str(response.content) if response.content else ""
        except Exception as e:
            logger.error(f"Error in Anthropic chat: {e}")
            raise


class LLMFactory:
    @staticmethod
    def create_llm(provider: LLMProvider = LLMProvider.ANTHROPIC) -> BaseLLM:
        if provider == LLMProvider.DEEPSEEK:
            return DeepSeekClient()
        elif provider == LLMProvider.ANTHROPIC:
            return AnthropicClient()
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")
