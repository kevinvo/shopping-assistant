"""
Centralized prompt management for the Shopping Assistant Agent.

This module provides a single source of truth for all LLM prompts used throughout
the application. Prompts are organized by domain:

- personas: Main assistant personas and system identities
- query_processing: Query rewriting and HyDE generation prompts
- evaluation: LLM-as-judge evaluation prompts for quality assessment
- suggested_prompts: Empty-state starter prompt generation
"""

from chalicelib.prompts.personas import PERSONA, get_persona
from chalicelib.prompts.query_processing import (
    CONTEXT_AWARE_PROMPT_REWRITING,
    PROMPT_REWRITE_INSTRUCTION,
    HYDE_GENERATION_PROMPT,
    HYDE_SYSTEM_PROMPT,
    REWRITE_JSON_SUFFIX,
    HYDE_USER_INSTRUCTION_SUFFIX,
    get_context_aware_rewrite,
    get_rewrite_user_instruction,
    get_hyde_system,
    get_hyde_user,
)
from chalicelib.prompts.evaluation import (
    FAITHFULNESS_SYSTEM_PROMPT,
    FAITHFULNESS_USER_PROMPT,
    ACTIONABILITY_SYSTEM_PROMPT,
    ACTIONABILITY_USER_PROMPT,
    RETRIEVAL_RELEVANCE_SYSTEM_PROMPT,
    RETRIEVAL_RELEVANCE_USER_PROMPT,
    get_faithfulness_system,
    get_faithfulness_user,
    get_actionability_system,
    get_actionability_user,
    get_retrieval_relevance_system,
    get_retrieval_relevance_user,
)
from chalicelib.prompts.suggested_prompts import (
    SUGGESTED_PROMPTS_SYSTEM_PROMPT,
    SUGGESTED_PROMPTS_USER_PROMPT,
)

__all__ = [
    # Personas
    "PERSONA",
    "get_persona",
    # Query processing
    "CONTEXT_AWARE_PROMPT_REWRITING",
    "PROMPT_REWRITE_INSTRUCTION",
    "HYDE_GENERATION_PROMPT",
    "HYDE_SYSTEM_PROMPT",
    "REWRITE_JSON_SUFFIX",
    "HYDE_USER_INSTRUCTION_SUFFIX",
    "get_context_aware_rewrite",
    "get_rewrite_user_instruction",
    "get_hyde_system",
    "get_hyde_user",
    # Evaluation
    "FAITHFULNESS_SYSTEM_PROMPT",
    "FAITHFULNESS_USER_PROMPT",
    "ACTIONABILITY_SYSTEM_PROMPT",
    "ACTIONABILITY_USER_PROMPT",
    "RETRIEVAL_RELEVANCE_SYSTEM_PROMPT",
    "RETRIEVAL_RELEVANCE_USER_PROMPT",
    "get_faithfulness_system",
    "get_faithfulness_user",
    "get_actionability_system",
    "get_actionability_user",
    "get_retrieval_relevance_system",
    "get_retrieval_relevance_user",
    # Suggested prompts (empty-state)
    "SUGGESTED_PROMPTS_SYSTEM_PROMPT",
    "SUGGESTED_PROMPTS_USER_PROMPT",
]
