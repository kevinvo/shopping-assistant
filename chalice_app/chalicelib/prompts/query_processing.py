"""
Query processing prompts for context-aware rewriting and HyDE generation.

Both prompts share the same CONTINUATION vs TOPIC SHIFT decision so they
react identically to a topic change. Without parallel handling, the
rewrite can correctly drop prior context while HyDE still embeds it,
re-introducing the dropped context into the search pool.

Each constant is the baked-in fallback. Prefer the matching `get_*()`
function so the active version comes from LangSmith Hub and the
@traceable run records which prompt version produced it.
"""

from typing import Tuple

from chalicelib.prompts.loader import get_prompt

CONTEXT_AWARE_PROMPT_REWRITING_HUB = "shopping-assistant-rewrite-system"
PROMPT_REWRITE_INSTRUCTION_HUB = "shopping-assistant-rewrite-user"
HYDE_SYSTEM_PROMPT_HUB = "shopping-assistant-hyde-system"
HYDE_GENERATION_PROMPT_HUB = "shopping-assistant-hyde-user"

CONTEXT_AWARE_PROMPT_REWRITING = """
You rewrite the user's latest message into a self-contained search query for a Reddit-style corpus.

Decide one of two cases:

CONTINUATION — the latest message clearly follows the most recent exchange. It uses a pronoun ("it", "this", "that"), topic ellipsis ("what about X?"), or qualifies the previous question.
  → Rewrite by expanding pronouns and ellipsis from the recent exchange.

TOPIC SHIFT — the latest message introduces a new product, entity, or concept that does not follow from the most recent exchange.
  → Rewrite using ONLY the new message. Do NOT carry over any prior topic.

When in doubt, choose TOPIC SHIFT. Wrong context biases the search; missing context is recoverable on the next turn.

# Examples

History:
  user: gift ideas for my girlfriend's birthday
  assistant: here are some gift ideas...
User: what about cards?
Rewritten: birthday card ideas for a girlfriend
(CONTINUATION — "cards" is topic ellipsis on a birthday-gift thread)

History:
  user: gift ideas for my girlfriend's birthday
  assistant: here are some gift ideas...
User: tell me about backpacks
Rewritten: tell me about backpacks
(TOPIC SHIFT — "backpacks" is a new product not previously discussed)

History:
  user: best running shoes for flat feet
  assistant: here are some recommendations...
User: what about for trail running?
Rewritten: best running shoes for flat feet for trail running
(CONTINUATION — "for trail running" qualifies the running-shoes query)

History:
  user: how to clean a cast iron pan
  assistant: here's how to clean it...
User: i need a new laptop for college
Rewritten: laptop for college
(TOPIC SHIFT — laptops are unrelated to cast iron pans)
""".strip()

PROMPT_REWRITE_INSTRUCTION = """
Apply the rule above to this user message: {query}
""".strip()

# Appended to PROMPT_REWRITE_INSTRUCTION to force JSON-mode output.
REWRITE_JSON_SUFFIX = '\n\nReturn JSON: {"rewritten_query": "..."}'


HYDE_SYSTEM_PROMPT = """
You generate a short keyword phrase summarizing the kind of Reddit discussion that would answer the user's latest message. The phrase will be embedded and used for vector search.

Decide one of two cases:

CONTINUATION — the latest message clearly follows the most recent exchange (pronoun, topic ellipsis, qualifies the prior question).
  → Generate keywords for the latest message in the context of that exchange.

TOPIC SHIFT — the latest message introduces a new product, entity, or concept that does not follow from the most recent exchange.
  → Generate keywords for ONLY the latest message. Do NOT carry over any prior topic.

When in doubt, choose TOPIC SHIFT.

# Examples

History:
  user: gift ideas for my girlfriend's birthday
  assistant: here are some gift ideas...
User: what about cards?
Keywords: birthday card designs, handmade pop-up cards, sentimental messages, scratch-off cards, personalized cardstock

History:
  user: gift ideas for my girlfriend's birthday
  assistant: here are some gift ideas...
User: tell me about backpacks
Keywords: backpack types, daypack vs commuter, water-resistant material, laptop sleeve, ergonomic straps, multiple compartments, durable construction

History:
  user: best running shoes for flat feet
  assistant: here are some recommendations...
User: what about for trail running?
Keywords: trail running shoes for flat feet, arch support, rugged outsole, lug pattern, waterproof options, motion control

History:
  user: how to clean a cast iron pan
  assistant: here's how to clean it...
User: i need a new laptop for college
Keywords: college laptop, lightweight, long battery life, programming-friendly, MacBook Air vs ThinkPad, RAM and storage trade-offs

Output: a comma-separated list, ~20 tokens. No sentences. No prefixes.
""".strip()

HYDE_GENERATION_PROMPT = """
Generate the keyword phrase for this user message: {query}

Output the comma-separated list directly. No prefix, no sentences, no
markdown — just the keywords.
""".strip()

# Empty by design: format spec is now folded into HYDE_GENERATION_PROMPT.
# Kept so client.py imports stay stable.
HYDE_USER_INSTRUCTION_SUFFIX = ""


def get_context_aware_rewrite() -> Tuple[str, str]:
    """Rewrite system prompt. No placeholders; call `.format()` to no-op."""
    return get_prompt(
        CONTEXT_AWARE_PROMPT_REWRITING_HUB,
        fallback=CONTEXT_AWARE_PROMPT_REWRITING,
    )


def get_rewrite_user_instruction() -> Tuple[str, str]:
    """Rewrite user prompt. Call `.format(query=...)`.

    The JSON-suffix braces are escaped (`{{`/`}}`) on Hub so `str.format`
    renders the literal JSON example in the output rather than treating
    it as another template variable. The fallback is the same escape so
    behavior is identical whether Hub or fallback wins.
    """
    fallback = PROMPT_REWRITE_INSTRUCTION + REWRITE_JSON_SUFFIX.replace(
        "{", "{{"
    ).replace("}", "}}")
    return get_prompt(PROMPT_REWRITE_INSTRUCTION_HUB, fallback=fallback)


def get_hyde_system() -> Tuple[str, str]:
    """HyDE system prompt. No placeholders; call `.format()` to no-op."""
    return get_prompt(HYDE_SYSTEM_PROMPT_HUB, fallback=HYDE_SYSTEM_PROMPT)


def get_hyde_user() -> Tuple[str, str]:
    """HyDE user prompt. Call `.format(query=...)`."""
    fallback = HYDE_GENERATION_PROMPT + HYDE_USER_INSTRUCTION_SUFFIX
    return get_prompt(HYDE_GENERATION_PROMPT_HUB, fallback=fallback)
