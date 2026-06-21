# Plan — Static Labeled Eval Set

Implements todo #1 (static labeled eval set) and folds in todo #2 (LLM-as-judge
calibration). Created 2026-06-20.

## Goal

A frozen, version-controlled set of cases with **human-applied ground-truth
labels**, plus a harness to (1) run cases through the real pipeline and capture
what was retrieved/answered, (2) label them, (3) score the system against the
labels — including calibrating the LLM-as-judge against human scores.

## Why this matters (the core problem)

Today there is **no ground truth**. Every score is an LLM-judge output or a
heuristic. The deterministic retrieval metrics define "relevant" as *the
reranker's own score ≥ 0.5* (`chalicelib/llm/metrics.py`,
`relevance_threshold=0.5`) — so recall@k / nDCG@k currently measure reranker
self-consistency, not truth. Human relevance labels break that circularity.

## Decisions

All decisions live in a single log: **`chalice_app/eval/DECISIONS.md`**
(D1 label scope, D2 storage, D3 case source, D4 capture approach, D5 relevance
scope, D6 location, D7 venv, D8 customer-query logging, D9 phasing). Add new
decisions there, not here.

## Layout

```
chalice_app/eval/
  README.md                      # how it works + labeling guide
  dataset/
    cases.jsonl                  # frozen cases (id, turns, source, tags)
    gold/
      retrieval.jsonl            # per case: relevant_doc_ids
      response.jsonl             # per case: expected_product_keywords, should_clarify
      judge_calibration.jsonl    # per case: human_faithfulness, human_actionability
  captured/                      # gitignored: pipeline outputs awaiting labels
    to_label.jsonl
chalice_app/scripts/
  eval_capture.py                # run cases through process_chat() → to_label.jsonl
  eval_score.py                  # gold labels → true recall/nDCG, response & judge metrics
  eval_sample_traces.py          # pull + scrub real traces → append to cases.jsonl
  eval_upload_langsmith.py       # mirror cases + gold to a LangSmith dataset
```

## Schemas

`cases.jsonl`:
```json
{"id":"curated-001","source":"curated","turns":["best running shoes for flat feet"],"tags":["single-turn"]}
{"id":"sampled-2026-06-20-003","source":"langsmith","turns":["..."],"tags":["sampled"],"origin_run_id":"<id>"}
```

`captured/to_label.jsonl` (what the human annotates):
```json
{"id":"curated-001","query":"...","rewritten_query":"...","response":"...","run_id":"...",
 "docs":[{"doc_id":"<md5>","rank":1,"text":"...","reranker_score":0.82,"relevant":null}]}
```

`gold/retrieval.jsonl`: `{"id":"curated-001","relevant_doc_ids":["<md5a>","<md5c>"]}`
`gold/response.jsonl`: `{"id":"curated-001","expected_product_keywords":["brooks","asics","stability"],"should_clarify":false}`
`gold/judge_calibration.jsonl`: `{"id":"curated-001","human_faithfulness":0.9,"human_actionability":0.7}`

## Capture approach (key technical choice)

`eval_capture.py` calls `Chat.process_chat()` **directly** (offline, using
`.env` creds for Qdrant + embeddings) and reads the returned `eval_metadata`
(`pre_rerank_results`, `reranker_scores`, `top_results`, `run_id`). This is
necessary because the retrieved doc texts are NOT returned to the WS client and
are not reliably persisted to LangSmith. (`run_eval_baseline.py`'s WS + poll
approach gives aggregate scores but not the raw docs we need to label.)

## Scoring (`eval_score.py`)

- **Retrieval:** feed human `relevant_doc_ids` into `RetrievalMetrics` →
  true recall@k / nDCG@k. Report side-by-side vs the reranker-threshold version
  to quantify the circularity gap.
- **Response:** expected-keyword presence in response; clarify-or-not match.
- **Judge calibration:** pull LLM-judge faithfulness/actionability for each
  case's `run_id` from LangSmith feedback; report MAE + correlation vs human.

## Known limitations (document honestly in README)

- Relevance is labeled over the *retrieved* pool only, so recall@k uses the same
  denominator as today (relevant-among-retrieved) — it does not catch relevant
  docs missing entirely from retrieval. Same scope as the current metric, now
  with true labels.
- Judge calibration sample size starts small (~25-50); report as directional.

## Phases (pause for review after Phase 1)

- **Phase 1 (foundation):** layout, `cases.jsonl` seeded from the 51 curated
  queries, `eval_capture.py`, README labeling guide, gitignore `captured/`.
  → **PAUSE for review.**
- **Phase 2:** `eval_score.py` (retrieval + response + judge calibration).
- **Phase 3:** `eval_sample_traces.py` (+ PII scrub) → add real-trace cases.
- **Phase 4:** `eval_upload_langsmith.py` → mirror to LangSmith dataset.
- **Phase 5:** `make eval-capture` / `make eval-score` targets; finalize README;
  update tasks/todo.md.

## Review section

### Phase 1 — foundation (done, awaiting review)

- `chalice_app/eval/dataset/cases.jsonl` — 25 curated cases (the existing
  baseline queries: 10 direct, 3 comparison, 2 vague/clarify, 3 topic-shift
  multi-turn, 7 niche), with stable ids + tags.
- `chalice_app/eval/dataset/gold/{retrieval,response,judge_calibration}.jsonl`
  — empty, schemas documented in the README.
- `chalice_app/eval/captured/.gitignore` — outputs regenerable, not committed.
- `chalice_app/scripts/eval_capture.py` — drives each case through
  `Chat.process_chat()`, captures the last turn's `eval_metadata`, joins
  reranker judgments to pre-rerank doc texts (best-ranked first) into a
  `to_label.jsonl` with null `relevant` fields.
- `chalice_app/eval/README.md` — workflow + all four schemas + labeling guide.

Verified: syntax compiles; cases.jsonl parses (25 unique ids, 3 multi-turn);
doc join/sort logic unit-checked in isolation.

**Live smoke run (3 cases) PASSED** — set up `.venv` (python3.12, pinned
requirements) since none existed and the base conda env had mismatched
langchain. `eval_capture --limit 3` produced well-formed `to_label.jsonl`:
real run_ids, rewritten/hyde queries, response, 15 docs each, ranks monotonic,
scores descending, all `relevant: null`. Confirms the input contract for the
Phase 2 scorer. (Observed: top reranker score 1.000 — the ≥0.5 self-threshold
circularity that human labels will replace.)

Env note: the project `.venv` must exist to run capture/score; `make test` and
the pipeline both need it (base conda env has langchain-core 1.2.7 vs pinned
0.3.76).

### Phase 2 — scorer (done)

- `chalice_app/scripts/eval_score.py` — reports three sections:
  1. **Retrieval** from human labels vs reranker-threshold baseline on the same
     captured ranking (recall@k, nDCG@k, MRR). Only fully-labeled cases scored;
     unlabeled skipped + counted. Labels from `gold/retrieval.jsonl` or the
     in-place `relevant` flags (D10).
  2. **Response** — expected-keyword recall + clarify-or-not accuracy.
  3. **Judge calibration** — re-runs faithfulness/actionability judges live,
     reports MAE + Pearson r vs human (`--no-judge` to skip the LLM calls).
- Pure stats helpers (keyword_recall, mae, pearson, clarify, label resolution)
  unit-tested in isolation — all pass.
- End-to-end smoke on the 3 captured cases with temporary labels: retrieval +
  response + judge sections all render; gold files reset to empty afterward.
- Confirmed the circularity it's meant to expose: reranker nDCG is trivially
  1.000 (ranking sorted by reranker score), while recall diverges (reranker
  marks far more docs "relevant").

**Finding (real bug surfaced by the smoke run):** the actionability judge's
output sometimes arrives wrapped in ```json fences, so `json.loads` fails and
`evaluate_actionability_llm` silently falls back to 0.5 (`evaluator.py:527`,
same risk at faithfulness:495 / retrieval:572). This corrupts judge scores in
production today. Candidate fix: strip code fences before parsing. Relates to
interview Q8 (judge validation). Tracked in tasks/todo.md.
