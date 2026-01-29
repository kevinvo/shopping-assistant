"""
Centralized prompt management for the Shopping Assistant Agent.

This module provides a single source of truth for all LLM prompts used throughout
the application. Prompts are organized by domain:

- personas: Main assistant personas and system identities
- query_processing: Query rewriting and HyDE generation prompts
- evaluation: LLM-as-judge evaluation prompts for quality assessment
"""

from chalicelib.prompts.personas import PERSONA
from chalicelib.prompts.query_processing import (
    CONTEXT_AWARE_PROMPT_REWRITING,
    PROMPT_REWRITE_INSTRUCTION,
    HYDE_GENERATION_PROMPT,
    HYDE_SYSTEM_PROMPT,
)
from chalicelib.prompts.evaluation import (
    FAITHFULNESS_SYSTEM_PROMPT,
    FAITHFULNESS_USER_PROMPT,
    ACTIONABILITY_SYSTEM_PROMPT,
    ACTIONABILITY_USER_PROMPT,
    RETRIEVAL_RELEVANCE_SYSTEM_PROMPT,
    RETRIEVAL_RELEVANCE_USER_PROMPT,
)

__all__ = [
    # Personas
    "PERSONA",
    # Query processing
    "CONTEXT_AWARE_PROMPT_REWRITING",
    "PROMPT_REWRITE_INSTRUCTION",
    "HYDE_GENERATION_PROMPT",
    "HYDE_SYSTEM_PROMPT",
    # Evaluation
    "FAITHFULNESS_SYSTEM_PROMPT",
    "FAITHFULNESS_USER_PROMPT",
    "ACTIONABILITY_SYSTEM_PROMPT",
    "ACTIONABILITY_USER_PROMPT",
    "RETRIEVAL_RELEVANCE_SYSTEM_PROMPT",
    "RETRIEVAL_RELEVANCE_USER_PROMPT",
]
