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
You are designing starter prompts for the empty-state of a shopping assistant
powered by Reddit community recommendations.

The assistant has indexed posts and discussions from these communities:
{subreddit_lines}

For EACH community listed above, generate exactly {prompts_per_subreddit}
starter prompts a real customer might type into THIS assistant. Every prompt:
- Maps to a product category that specific community actually covers
- Is 6-12 words, action-oriented, and specific
- Is a question or imperative the user would say (no meta commentary)
- Is distinct from every other prompt across all communities (no near-duplicates)
- Varies its framing across the set: budget-conscious, gift-finding, comparison,
  recommendation, or alternative-to-a-popular-brand

Return strictly valid JSON: a single object whose keys are the community names
exactly as written above (including the "r/" prefix) and whose values are arrays
of exactly {prompts_per_subreddit} prompt strings. No prose, no markdown fences,
no commentary outside the JSON:

{{"r/example": ["...", "..."], "r/another": ["...", "..."]}}
""".strip()
