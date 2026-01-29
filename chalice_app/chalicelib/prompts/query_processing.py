"""
Query processing prompts for context-aware rewriting and HyDE generation.

These prompts are used to:
1. Rewrite user queries with conversation context awareness
2. Generate hypothetical document embeddings (HyDE) for improved search
"""

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
