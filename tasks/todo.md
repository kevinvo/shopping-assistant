# Project TODO — Shopping Assistant Agent

Last updated: 2026-06-20

Source of truth for outstanding work. Grouped by horizon. Interview-prep gaps
(from `tasks/ai-system-interview-questions.md`) are the same as the real
engineering backlog — the ⚠️ items there are listed here as concrete tasks.

---

## Now — housekeeping / in-flight

- [ ] Commit uncommitted work on `feat/two-prompts-per-subreddit`
      (`chalicelib/services/suggested_prompts.py` + `tests/test_suggested_prompts.py`)
- [ ] Branch is `gone` on origin (deleted/merged) — rebase work onto `main` and re-push / open fresh PR
- [ ] Sync local `main` (currently 3 behind `origin/main`)
- [ ] Prune stale merged local branches (`claude/*`, merged `fix/*`)

---

## High value — quality measurement (interview focus area)

- [ ] **Build a static labeled eval set** (Q2 ⚠️)
      Sample N real traces from the LangSmith per-request feedback stream,
      human-label them, freeze as a regression set.
      *Highest-leverage item — also fixes the weakest interview answer.*
- [ ] **Validate the LLM-as-judge against human labels** (Q8 ⚠️)
      Calibrate `evaluator.py` judge scores against the labeled set above.
      Confirm which model judges (likely `deepseek-chat:nitro`).
- [ ] **Run a HyDE ablation** (Q10 ⚠️)
      Measure retrieval lift (with vs without HyDE) using existing
      `retrieval_relevance` + `recall_at_k` / `ndcg_at_k` signals.

---

## Engineering practice — CI/CD & safety

- [ ] **Gate deploys on the smoke battery** (Q4, Q19)
      Wire `scripts/prod_smoke_topic_shift.py` into `deploy-prod.yml` as a
      deploy gate (currently manual).
- [ ] **Add adversarial / prompt-injection defense** (Q16 ⚠️)
      No specific defense today. Decide on input filtering / guardrails.
- [ ] **Define PII / data-retention policy** (Q17 ⚠️)
      TTL + deletion story for conversation histories in DynamoDB.
- [ ] **Track topic-adherence as a per-request metric** (Q6)
      Add a `topic_adherence` key to the LangSmith feedback instead of only
      the manual 10-case battery.

---

## Performance — "what's still on the table" (README)

- [ ] **Move Lambda region to match Qdrant** (`eu-west-2`)
      Biggest latency win (~1–2s warm, 3–5s cold) but heavy migration:
      DynamoDB Global Tables, S3 CRR, multi-region Secrets, re-point frontend WebSocket URL.
- [ ] **Provisioned concurrency on `chat_processor`**
      Only if the 3-min keep-warm cadence proves insufficient under real traffic gaps.
- [ ] **Drop LangChain from the hot path**
      ~5–8s cold-init + ~50–150ms warm/LLM-call savings. Big refactor to direct SDK calls.

---

## Review section

_(Add a summary here after completing batches of work.)_
