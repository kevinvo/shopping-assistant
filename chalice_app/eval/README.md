# Static Labeled Eval Set

A frozen, version-controlled set of cases with **human ground-truth labels** for
the shopping-assistant RAG pipeline. It exists to break the circularity in the
current metrics: today "relevant" means *the reranker's own score ≥ 0.5*
(`chalicelib/llm/metrics.py`), so recall@k / nDCG@k measure reranker
self-consistency, not truth. Human labels give us real numbers and let us
calibrate the LLM-as-judge against human scores.

See `tasks/eval-set-plan.md` for the full plan and phase breakdown.

## Layout

```
eval/
  dataset/
    cases.jsonl                  # frozen cases (committed, source of truth)
    gold/
      retrieval.jsonl            # human relevance labels
      response.jsonl             # expected products + clarify flag
      judge_calibration.jsonl    # human faithfulness/actionability scores
  captured/                      # gitignored; regenerable pipeline outputs
    to_label.jsonl
```

## Workflow

1. **Capture** — run the cases through the real pipeline:
   ```
   cd chalice_app
   python -m scripts.eval_capture            # or --limit N for a smoke run
   ```
   This calls `Chat.process_chat()` directly (real Qdrant + embeddings/LLM
   calls via your `.env`) and writes `captured/to_label.jsonl`.

2. **Label** — interactive helper writes the `gold/` files directly:
   ```
   python -m scripts.eval_label --limit 5      # label the first 5 cases
   python -m scripts.eval_label --ids curated-001,curated-014
   ```
   Per doc: `y`=relevant, `n`=not, `s`=skip, `q`=save & quit. It also asks for
   expected product keywords, should-clarify, and your faithfulness /
   actionability scores. Resumable — already-labeled items are skipped. You stay
   the sole author of every label (the dataset's value is human ground truth).

3. **Score** — `python -m scripts.eval_score` reads `gold/` and reports true
   recall@k / nDCG@k vs the reranker baseline, response metrics, and judge
   agreement (`--no-judge` to skip the live judge LLM calls).

## Schemas

### `cases.jsonl` (one JSON object per line)
```json
{"id": "curated-001", "source": "curated", "turns": ["best running shoes for flat feet"], "tags": ["single-turn", "direct"]}
```
- `turns` — list of user messages; the **last** turn is the one scored, earlier
  turns just establish conversation context (e.g. topic-shift cases).
- `source` — `curated` or `langsmith` (sampled real traffic, added in Phase 3).

### `captured/to_label.jsonl` (produced by `eval_capture`, you annotate it)
```json
{"id": "curated-001", "query": "...", "rewritten_query": "...", "response": "...",
 "run_id": "...",
 "docs": [{"doc_id": "<md5>", "rank": 1, "reranker_score": 0.82, "text": "...", "relevant": null}]}
```
Set each doc's `relevant` to `true` / `false`.

### `gold/retrieval.jsonl`
```json
{"id": "curated-001", "relevant_doc_ids": ["<md5a>", "<md5c>"]}
```
The doc_ids you marked `relevant: true`. **Relevance is judged over the
retrieved pool only** — so recall@k uses the same denominator as the current
metric (relevant-among-retrieved); it does not catch relevant docs that
retrieval missed entirely.

### `gold/response.jsonl`
```json
{"id": "curated-001", "expected_product_keywords": ["brooks", "asics", "stability"], "should_clarify": false}
```
- `expected_product_keywords` — substrings a good answer should mention
  (brands, models, key features). Case-insensitive.
- `should_clarify` — `true` for vague queries where the right move is to ask a
  clarifying question instead of recommending (e.g. "I need a gift").

### `gold/judge_calibration.jsonl`
```json
{"id": "curated-001", "human_faithfulness": 0.9, "human_actionability": 0.7}
```
Your own 0.0–1.0 scores, compared in Phase 2 against the LLM judge
(`chalicelib/jobs/evaluator.py`) to measure agreement (MAE + correlation).

## Labeling guidance

- **Relevant doc** = on-topic for the query and could plausibly support a
  recommendation. A doc about the wrong category is not relevant even if it
  mentions a product.
- **Faithfulness** = is the response grounded in the retrieved Reddit context,
  with no invented facts? 1.0 = fully grounded, 0.0 = hallucinated.
- **Actionability** = does it give specific, usable recommendations? A correct
  clarifying question on a vague query is actionable (score high), a vague
  non-answer is not.
- Keep labels stable — this set is a regression baseline; re-label only when a
  case's intent genuinely changes.
