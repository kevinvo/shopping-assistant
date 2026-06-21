# Eval Set — Decision Log

Architecture decisions for the static labeled eval set. Newest context in
`tasks/eval-set-plan.md`; this file is the durable "why" record. Dates are when
the decision was made.

---

## D1 — Label scope: full (retrieval + response + judge calibration)
**2026-06-20.** Labels cover three things: (a) per-doc retrieval relevance,
(b) response-level expectations (expected product keywords + clarify-or-not),
(c) human faithfulness/actionability scores for calibrating the LLM judge.
**Why:** folds todo #2 (judge calibration) into the same labeling pass and
gives ground truth for both retrieval and response quality. Heaviest option,
but the labeling effort overlaps so the marginal cost is low.
**Alternatives:** retrieval-only (smallest, rejected as too narrow);
retrieval+response without judge calibration (rejected — leaves todo #2 open).

## D2 — Storage: both checked-in jsonl AND LangSmith mirror
**2026-06-20.** The checked-in `eval/dataset/*.jsonl` is the **source of
truth** (durable, diffable, offline, reviewable in PRs). A LangSmith dataset is
a **mirror** for the annotation UI + native eval integration (Phase 4).
**Why:** "static" means version-controlled; LangSmith alone puts labels outside
the repo. Mirror gives the UI without giving up the source-of-truth property.

## D3 — Case source: both curated + sampled real traces
**2026-06-20.** v1 seeds from the 25 curated baseline queries
(`run_eval_baseline.py`); Phase 3 augments with sampled real LangSmith traces
(with a PII scrub step).
**Why:** curated is reproducible and PII-free for a fast start; real traces add
true traffic distribution. Doing both avoids over-fitting to hand-picked cases.

## D4 — Capture via direct `process_chat()` call, not the WebSocket path
**2026-06-20.** `eval_capture.py` imports `Chat` and calls `process_chat()`
directly, reading the returned `eval_metadata`.
**Why:** the raw retrieved-document texts + doc_ids are needed to label
relevance, and they are NOT sent to the WS client nor reliably persisted to
LangSmith. `run_eval_baseline.py`'s WS+poll approach only yields aggregate
judge scores. Cost: real Qdrant + embeddings/LLM calls per case.
**Validated:** live 3-case smoke produced well-formed output (2026-06-20).

## D5 — Relevance labeled over the retrieved pool only
**2026-06-20.** Humans label relevance of the docs that were retrieved; recall@k
therefore uses the same denominator as the current metric
(relevant-among-retrieved).
**Why:** labeling true corpus-wide relevance is infeasible. This keeps scope
identical to today's metric — we only swap the reranker's self-score for human
truth, which is the whole point (breaks the ≥0.5 circularity in
`chalicelib/llm/metrics.py`). **Limitation:** does not detect relevant docs that
retrieval missed entirely. Documented in the README.

## D6 — Dataset location: `chalice_app/eval/`
**2026-06-20.** Data under `chalice_app/eval/dataset/`, scripts as
`chalice_app/scripts/eval_*.py` (next to `run_eval_baseline.py`), regenerable
captures under `chalice_app/eval/captured/` (gitignored).
**Why:** keeps eval data with the app it evaluates and scripts with the existing
eval runner.

## D7 — Project `.venv` is required to run capture/score
**2026-06-20.** Created `.venv` (python3.12, pinned `requirements.txt`) because
none existed and the base conda env had `langchain-core 1.2.7` vs the pinned
`0.3.76`, which breaks all langchain-importing modules (also why `make test`
errored). The Makefile already sources `.venv/bin/activate`.
**Why:** the pinned env is the only one the pipeline imports cleanly in.

## D8 — `log_customer_query` during capture: leave ON for now (deferred)
**2026-06-20.** `process_chat()` calls `log_customer_query()`, so each capture
run adds ~25 rows to the `customer-queries` LangSmith dataset.
**Decision:** leave it on for now — low volume, harmless, and it gives us real
run_ids/traces. Revisit a `--no-log` suppression flag only if dataset noise
becomes a problem. **Status: open / low priority.**

## D10 — Scorer label sources + metric semantics
**2026-06-21.** `eval_score.py`:
- **Retrieval labels** read from `gold/retrieval.jsonl` if present, else fall
  back to the in-place `relevant` flags in `captured/to_label.jsonl`. Only
  **fully-labeled** cases (no null docs) enter the retrieval aggregate;
  partially-labeled cases are counted and skipped. Metrics are computed over the
  **captured reranked order**, comparing human labels (1.0/0.0) against the
  reranker-threshold baseline on the identical ordering — the side-by-side that
  exposes the circularity. (Differs from production, which scores the pre-rerank
  order; v1 only captures the reranked list per D5.)
- **Response labels** (`expected_product_keywords`, `should_clarify`) and
  **judge labels** (`human_faithfulness`, `human_actionability`) come from
  `gold/*.jsonl` — pure human input, not derivable from a capture.
- **Judge calibration** re-runs the evaluator's judge functions live (the
  captured runs never hit the SQS evaluator, so no feedback exists to pull).
  Faithfulness context is reconstructed from the top captured doc texts (the
  exact `search_context` isn't captured) — documented approximation.
- **Follow-up:** a freeze step to commit drift-aware retrieval labels into
  `gold/retrieval.jsonl` keyed by doc_id (Phase 5).

## D9 — Phased delivery with review gate after Phase 1
**2026-06-20.** Phase 1 foundation → review → Phase 2 scorer → Phase 3 trace
sampling → Phase 4 LangSmith mirror → Phase 5 make-targets/docs.
**Why:** the foundation's capture contract had to be proven before building the
scorer against it. Phase 1 reviewed and approved 2026-06-20; smoke passed.
