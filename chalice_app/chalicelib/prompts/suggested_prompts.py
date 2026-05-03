"""
Prompts for generating empty-state starter suggestions.

Used by the suggested_prompts cron to produce starter prompts grounded in the
indexed Reddit communities. Templates are filled via `.format(...)` from the
service layer.
"""

SUGGESTED_PROMPTS_SYSTEM_PROMPT = """
You output strictly valid JSON. No prose outside the JSON object. No markdown
code fences.
""".strip()


SUGGESTED_PROMPTS_USER_PROMPT = """
You are designing {target_count} starter prompts for the empty-state of a
shopping assistant powered by Reddit community recommendations.

The assistant has indexed posts and discussions from these communities:
{subreddit_lines}

Generate exactly {target_count} starter prompts a real customer might type
into THIS assistant, where every prompt:
- Maps to a product category these communities actually cover
- Is 6-12 words, action-oriented, and specific
- Avoids topics absent from these communities
- Mixes framings: budget-conscious, gift-finding, comparison, recommendation,
  alternative-to-popular-brand
- Is a question or imperative the user would say (no meta commentary)
- Spreads roughly evenly across distinct product categories represented in the
  community list (don't cluster all prompts in one niche)

Return strictly valid JSON in this shape, with no prose, no markdown fences,
no commentary outside the JSON:

{{"prompts": ["...", "...", "..."]}}
""".strip()
