"""Phase 2 of the static labeled eval set: score the system against human
ground-truth labels.

Reads the captured pipeline outputs + human labels and reports three things:

  1. Retrieval quality from HUMAN labels -- recall@k, nDCG@k, MRR, hit@k --
     side-by-side with the reranker-threshold baseline computed on the same
     ranking. The gap is the circularity that human labels remove (today
     "relevant" just means reranker score >= 0.5).
  2. Response quality -- expected-product-keyword recall + clarify-or-not
     accuracy on vague queries.
  3. LLM-as-judge calibration -- re-runs the evaluator's faithfulness /
     actionability judges and compares them to human scores (MAE + Pearson r).

Label sources (see eval/DECISIONS.md D10):
  - retrieval: gold/retrieval.jsonl if present, else the in-place `relevant`
    flags in captured/to_label.jsonl. Only fully-labeled cases are scored.
  - response: gold/response.jsonl   (expected_product_keywords, should_clarify)
  - judge:    gold/judge_calibration.jsonl  (human_faithfulness/actionability)

Run:
    cd chalice_app
    python -m scripts.eval_score                 # full
    python -m scripts.eval_score --no-judge      # skip live judge LLM calls

Inputs:  eval/captured/to_label.jsonl  +  eval/dataset/gold/*.jsonl
Output:  stdout report  (+ eval/captured/scores.json)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
CAPTURED_PATH = EVAL_DIR / "captured" / "to_label.jsonl"
GOLD_DIR = EVAL_DIR / "dataset" / "gold"
REPORT_PATH = EVAL_DIR / "captured" / "scores.json"

K_VALUES = [5, 10, 15]
RELEVANCE_THRESHOLD = 0.5
# How many top captured docs to reconstruct the faithfulness context from. The
# exact search_context the response saw isn't captured; this approximates it.
CONTEXT_DOCS = 5


# --------------------------------------------------------------------------- #
# Pure helpers (no heavy imports -- unit-testable on their own)
# --------------------------------------------------------------------------- #
def keyword_recall(response: str, expected: List[str]) -> Optional[float]:
    """Fraction of expected keywords present in the response (case-insensitive)."""
    if not expected:
        return None
    low = response.lower()
    hits = sum(1 for kw in expected if kw.lower() in low)
    return hits / len(expected)


def asked_clarifying_question(response: str) -> bool:
    """Heuristic: a clarifying response asks a question."""
    return "?" in response


def mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def mae(pairs: List[Tuple[float, float]]) -> Optional[float]:
    """Mean absolute error between (human, machine) score pairs."""
    if not pairs:
        return None
    return sum(abs(h - m) for h, m in pairs) / len(pairs)


def pearson(pairs: List[Tuple[float, float]]) -> Optional[float]:
    """Pearson correlation; None if <2 points or zero variance on either side."""
    n = len(pairs)
    if n < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _retrieval_labels_for(
    case_id: str, captured: Dict[str, Any], gold_retrieval: Dict[str, Dict[str, bool]]
) -> Optional[Dict[str, bool]]:
    """doc_id -> relevant. Prefer gold/retrieval.jsonl; fall back to the
    in-place `relevant` flags. Returns None if any captured doc is unlabeled
    (case is not fully labeled and is excluded from the retrieval aggregate)."""
    docs = captured["docs"]
    if case_id in gold_retrieval:
        labels = gold_retrieval[case_id]
        if all(d["doc_id"] in labels for d in docs):
            return {d["doc_id"]: labels[d["doc_id"]] for d in docs}
        return None
    if all(d.get("relevant") is not None for d in docs):
        return {d["doc_id"]: bool(d["relevant"]) for d in docs}
    return None


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_retrieval(
    captured: Dict[str, Any], labels: Dict[str, bool]
) -> Dict[str, Dict[str, float]]:
    """Compute retrieval metrics from human labels and from the reranker
    threshold, over the same captured ranking."""
    from dataclasses import asdict

    from chalicelib.llm.metrics import RetrievalMetrics

    rm = RetrievalMetrics(relevance_threshold=RELEVANCE_THRESHOLD)
    docs = captured["docs"]
    retrieved_docs = [{"doc_id": d["doc_id"], "text": d["text"]} for d in docs]

    human_judgments = [
        {"doc_id": d["doc_id"], "relevance_score": 1.0 if labels[d["doc_id"]] else 0.0}
        for d in docs
    ]
    reranker_judgments = [
        {"doc_id": d["doc_id"], "relevance_score": d["reranker_score"]} for d in docs
    ]
    human = rm.compute_all_metrics(retrieved_docs, human_judgments, K_VALUES)
    baseline = rm.compute_all_metrics(retrieved_docs, reranker_judgments, K_VALUES)
    return {"human": asdict(human), "reranker_baseline": asdict(baseline)}


def score_judge(
    captured: Dict[str, Any], human: Dict[str, float]
) -> Dict[str, Dict[str, float]]:
    """Re-run the LLM judges and pair them with human scores."""
    from chalicelib.jobs.evaluator import (
        evaluate_faithfulness,
        evaluate_actionability_llm,
    )

    query = captured["query"]
    response = captured["response"]
    context = "\n\n".join(d["text"] for d in captured["docs"][:CONTEXT_DOCS])

    out: Dict[str, Dict[str, float]] = {}
    if "human_faithfulness" in human:
        llm = evaluate_faithfulness(query, context, response).faithfulness
        out["faithfulness"] = {"human": human["human_faithfulness"], "llm": llm}
    if "human_actionability" in human:
        llm = evaluate_actionability_llm(query, response).actionability
        out["actionability"] = {"human": human["human_actionability"], "llm": llm}
    return out


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _avg_metric(rows: List[Dict[str, Any]], side: str, key: str) -> Optional[float]:
    vals = [r["retrieval"][side][key] for r in rows if r.get("retrieval")]
    return mean([v for v in vals if v is not None])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-judge", action="store_true", help="skip live judge LLM calls"
    )
    parser.add_argument("--captured", type=Path, default=CAPTURED_PATH)
    args = parser.parse_args()

    captured_rows = _load_jsonl(args.captured)
    if not captured_rows:
        print(f"No captured data at {args.captured}. Run eval_capture first.")
        return 1
    captured_by_id = {r["id"]: r for r in captured_rows}

    gold_retrieval = {
        r["id"]: {lbl["doc_id"]: bool(lbl["relevant"]) for lbl in r.get("labels", [])}
        for r in _load_jsonl(GOLD_DIR / "retrieval.jsonl")
    }
    gold_response = {r["id"]: r for r in _load_jsonl(GOLD_DIR / "response.jsonl")}
    gold_judge = {r["id"]: r for r in _load_jsonl(GOLD_DIR / "judge_calibration.jsonl")}

    results: List[Dict[str, Any]] = []
    retrieval_scored = retrieval_skipped = 0
    judge_pairs: Dict[str, List[Tuple[float, float]]] = {
        "faithfulness": [],
        "actionability": [],
    }

    for case_id, cap in captured_by_id.items():
        row: Dict[str, Any] = {"id": case_id}

        labels = _retrieval_labels_for(case_id, cap, gold_retrieval)
        if labels is None:
            retrieval_skipped += 1
        else:
            row["retrieval"] = score_retrieval(cap, labels)
            retrieval_scored += 1

        if case_id in gold_response:
            g = gold_response[case_id]
            row["response"] = {
                "keyword_recall": keyword_recall(
                    cap["response"], g.get("expected_product_keywords", [])
                ),
            }
            if "should_clarify" in g:
                row["response"]["clarify_correct"] = (
                    asked_clarifying_question(cap["response"]) == g["should_clarify"]
                )

        if not args.no_judge and case_id in gold_judge:
            jr = score_judge(cap, gold_judge[case_id])
            row["judge"] = jr
            for metric, pair in jr.items():
                if metric in judge_pairs:
                    judge_pairs[metric].append((pair["human"], pair["llm"]))

        results.append(row)

    # ---- print report ----
    print(f"\n=== Eval scores ({len(results)} captured cases) ===\n")

    print(
        f"-- Retrieval (human truth vs reranker self-threshold) --"
        f"  scored {retrieval_scored}, skipped {retrieval_skipped} (unlabeled)"
    )
    if retrieval_scored:
        print(f"{'metric':<16}{'human':>10}{'reranker':>10}")
        for k in K_VALUES:
            for name in (f"recall_at_{k}", f"ndcg_at_{k}"):
                h = _avg_metric(results, "human", name)
                b = _avg_metric(results, "reranker_baseline", name)
                print(f"{name:<16}{_fmt(h):>10}{_fmt(b):>10}")
        for name in ("mrr",):
            h = _avg_metric(results, "human", name)
            b = _avg_metric(results, "reranker_baseline", name)
            print(f"{name:<16}{_fmt(h):>10}{_fmt(b):>10}")

    resp_rows = [r for r in results if r.get("response")]
    print(f"\n-- Response --  scored {len(resp_rows)}")
    if resp_rows:
        kr = mean(
            [
                r["response"]["keyword_recall"]
                for r in resp_rows
                if r["response"].get("keyword_recall") is not None
            ]
        )
        clar = [
            r["response"]["clarify_correct"]
            for r in resp_rows
            if "clarify_correct" in r["response"]
        ]
        print(f"keyword_recall   {_fmt(kr)}")
        if clar:
            print(f"clarify_accuracy {_fmt(sum(clar)/len(clar))}  (n={len(clar)})")

    if not args.no_judge:
        print("\n-- Judge calibration (human vs LLM) --")
        for metric, pairs in judge_pairs.items():
            if pairs:
                print(
                    f"{metric:<14} MAE={_fmt(mae(pairs))}  "
                    f"r={_fmt(pearson(pairs))}  (n={len(pairs)})"
                )
        if not any(judge_pairs.values()):
            print("(no judge labels yet)")

    REPORT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {REPORT_PATH}")
    if not retrieval_scored and not resp_rows:
        print("\nNo labels yet -- add gold/ entries or label to_label.jsonl.")
    return 0


def _fmt(x: Optional[float]) -> str:
    return "  -  " if x is None else f"{x:.3f}"


if __name__ == "__main__":
    sys.exit(main())
