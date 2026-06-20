# Likely Interview Questions — AI System

Questions an experienced reviewer is likely to ask about this shopping-assistant codebase. Quality measurement is the focus area; surrounding questions probe rigor.

Items marked ⚠️ are areas where the current implementation doesn't have a strong answer — pre-rehearse these specifically.

---

## Quality measurement (the focus area)

1. **How do you actually measure response quality today?**
   **You actually have a real story here.** Every chat request triggers an async evaluator (`chalicelib/jobs/evaluator.py`) that posts an `EvaluationScores` record to LangSmith. Per-request signals captured:
   - **LLM-as-judge:** faithfulness, actionability_llm, retrieval_relevance (rubric prompts in `chalicelib/prompts/evaluation.py`)
   - **Deterministic retrieval:** recall@5/10/15, nDCG@5/10/15
   - **Heuristics:** has_products, has_specifics, response_length, heuristic_score, evaluation_tier
   Lead with this; it's stronger than typical of single-engineer projects.

2. **Do you have a labeled eval set?**
   ⚠️ Not a static labeled set — but you DO have a per-request LangSmith feedback stream that accumulates the LLM-as-judge scores against real traffic. You can sample / aggregate from it. Worth turning into a static eval set as the next step.

3. **How do you separately measure retrieval quality vs response quality?**
   Already separated in `EvaluationScores`: `retrieval_relevance` + `recall_at_k` + `ndcg_at_k` for retrieval; `faithfulness` + `actionability_llm` for response.

4. **How do you detect quality regressions on a prompt or model change?**
   Topic-shift smoke battery (`scripts/prod_smoke_topic_shift.py`) + LangSmith score deltas before/after deploy. Caught the gpt-4o-mini pattern-copy quirk on case 4 last battery run — solid war story.

5. **What's your hallucination rate / how do you measure groundedness?**
   `faithfulness` LLM-as-judge score per response. The PERSONA also constrains "only base on Reddit discussions" — and the eval validates the model follows that. (Have a number ready: pull recent faithfulness aggregate from LangSmith.)

6. **What's your topic-adherence metric specifically?**
   10-case battery + bleed-keyword check. Reproduces and prevents the original gift→backpacks regression. Could also be tracked via per-request LangSmith feedback by adding a "topic_adherence" key.

7. **What's your eval cadence?**
   Per-request (every chat triggers `trigger_async_evaluation`). Plus the smoke battery before deploys. Be honest that the battery is manual today.

8. **LLM-as-judge — which models, validated how?**
   Look at `evaluator.py` to confirm which model judges (likely deepseek-chat:nitro since that's the default for `chat()`). ⚠️ Validation of the judge against human labels is the usual gotcha — have an answer for "how do you know your judge is right?".

---

## RAG / retrieval

9. **Why hybrid search?** What % of useful results come from BM25 vs dense in practice?

10. **Is HyDE actually helping?** Have you measured retrieval lift from HyDE vs just the raw query?
    ⚠️ No ablation yet.

11. **What happens when the corpus genuinely doesn't cover a question?**
    Strong fresh answer: the responder refusal-threshold story (PR #37). Tightened the PERSONA so "I don't have enough information" only fires when nothing in the search context is on-topic.

12. **How fresh is the corpus and how do you choose subreddits?**

---

## Model & cost

13. **Why gpt-4o-mini for rewrite, deepseek-chat:nitro for response?**
    Cost/latency trade-offs. Receipts in PRs #27, #29, #30.

14. **Cost per query?**
    Topic-shift few-shot added ~+0.0001/req. Have a rough total ready.

15. **Tail latency — P50/P95/P99 TTFT?**
    Streaming push gave good answers — message_start at t=0, parallel fan-out, BM25 vocab on S3.

---

## Safety / failure modes

16. **Adversarial / off-topic / prompt-injection inputs?**
    ⚠️ Probably no specific defense today.

17. **PII / user data retention?**
    Conversation histories in DynamoDB — what TTL, deletion story.

18. **Most common production failure?**
    Have concrete CloudWatch examples ready.

---

## Engineering practice

19. **CI/CD for prompt changes** — version control, rollback, deploy gating.
    Smoke harness is manual, not CI-gated. (Could add to deploy-prod.yml as a gate.)

20. **How do you decide what to improve next?** Bug-driven, user-driven, metric-driven?
    Honest answer: bug-driven. Plan to add proactive eval.

---

## Top 4 to pre-rehearse a 30-second answer for

1. "How do you measure quality" — lead with `EvaluationScores`: per-request LangSmith feedback, LLM-as-judge for faithfulness/actionability/retrieval-relevance, deterministic recall/nDCG. Then admit you don't yet have a static labeled eval set, and frame that as the next step.
2. "How do you validate your LLM-as-judge?" — the usual gotcha when you admit using LLM-as-judge. Pre-rehearse honest answer (probably "haven't yet, will sample N traces and human-label them to calibrate").
3. "How do you detect regressions" — topic-shift war story + smoke battery + LangSmith score deltas.
4. "Is HyDE actually helping?" — frame as open question worth an ablation; you have the infrastructure (retrieval_relevance + recall_at_k) to actually answer it.
