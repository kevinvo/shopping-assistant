"""Interactive labeling helper for the static labeled eval set.

Walks you through captured cases one document at a time and writes the
committed gold files. You stay the sole author of every label (the dataset's
value is HUMAN ground truth -- see eval/DECISIONS.md D1).

Per case it collects:
  - retrieval: relevant? y/n for each retrieved doc        -> gold/retrieval.jsonl
  - response:  expected product keywords + should-clarify  -> gold/response.jsonl
  - judge:     your faithfulness / actionability (0-1)     -> gold/judge_calibration.jsonl

Resumable: docs/cases already in the gold files are pre-filled and skipped
unless you pass --relabel. Progress is saved after every case, so quitting
(q) mid-run keeps what you've done.

Run:
    cd chalice_app
    python -m scripts.eval_label --limit 5        # first 5 cases (the pilot)
    python -m scripts.eval_label --ids curated-001,curated-014

Then score:
    python -m scripts.eval_score
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
CAPTURED_PATH = EVAL_DIR / "captured" / "to_label.jsonl"
GOLD_DIR = EVAL_DIR / "dataset" / "gold"
DOC_PREVIEW_CHARS = 500


class QuitLabeling(Exception):
    """Raised to break out and save."""


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        raise QuitLabeling()


def _ask_relevant(doc: Dict[str, Any]) -> Optional[bool]:
    """y/n = relevant?, s = skip (leave unlabeled), q = save & quit."""
    preview = doc["text"][:DOC_PREVIEW_CHARS].strip().replace("\n", " ")
    print(f"\n  rank {doc['rank']:>2} | reranker={doc['reranker_score']:.2f}")
    print(f"  {preview}")
    while True:
        ans = _prompt("  relevant? [y/n/s/q] > ").lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        if ans in ("s", "skip", ""):
            return None
        if ans in ("q", "quit"):
            raise QuitLabeling()
        print("  (y=yes, n=no, s=skip, q=save & quit)")


def _ask_float(label: str) -> Optional[float]:
    while True:
        ans = _prompt(f"  {label} (0-1, blank=skip) > ")
        if ans == "":
            return None
        try:
            v = float(ans)
            if 0.0 <= v <= 1.0:
                return v
        except ValueError:
            pass
        print("  (enter a number between 0 and 1, or blank)")


def _ask_yes_no(label: str) -> Optional[bool]:
    ans = _prompt(f"  {label} [y/n/blank] > ").lower()
    if ans in ("y", "yes"):
        return True
    if ans in ("n", "no"):
        return False
    return None


def label_case(
    cap: Dict[str, Any],
    gold_ret: Dict[str, Dict[str, bool]],
    gold_resp: Dict[str, Dict[str, Any]],
    gold_judge: Dict[str, Dict[str, Any]],
    relabel: bool,
) -> None:
    cid = cap["id"]
    print("\n" + "=" * 72)
    print(f"CASE {cid}   tags={cap.get('tags')}")
    print(f"query:    {cap['query']!r}")
    print(f"response: {cap['response'][:200].strip()}...")

    # --- retrieval ---
    existing = gold_ret.get(cid, {})
    labels = dict(existing)
    for doc in cap["docs"]:
        did = doc["doc_id"]
        if not relabel and did in labels:
            continue
        verdict = _ask_relevant(doc)
        if verdict is not None:
            labels[did] = verdict
    gold_ret[cid] = labels

    # --- response ---
    if relabel or cid not in gold_resp:
        print("\n  -- response labels --")
        kw = _prompt("  expected product keywords (comma-separated) > ")
        keywords = [k.strip() for k in kw.split(",") if k.strip()]
        clarify = _ask_yes_no("  should the assistant ask a clarifying question?")
        entry: Dict[str, Any] = {"expected_product_keywords": keywords}
        if clarify is not None:
            entry["should_clarify"] = clarify
        gold_resp[cid] = entry

    # --- judge calibration ---
    if relabel or cid not in gold_judge:
        print("\n  -- judge calibration (your scores) --")
        f = _ask_float("faithfulness")
        a = _ask_float("actionability")
        entry = {}
        if f is not None:
            entry["human_faithfulness"] = f
        if a is not None:
            entry["human_actionability"] = a
        if entry:
            gold_judge[cid] = entry


def _save(gold_ret, gold_resp, gold_judge) -> None:
    _write_jsonl(
        GOLD_DIR / "retrieval.jsonl",
        [
            {
                "id": cid,
                "labels": [{"doc_id": d, "relevant": v} for d, v in lbl.items()],
            }
            for cid, lbl in gold_ret.items()
            if lbl
        ],
    )
    _write_jsonl(
        GOLD_DIR / "response.jsonl",
        [{"id": cid, **e} for cid, e in gold_resp.items()],
    )
    _write_jsonl(
        GOLD_DIR / "judge_calibration.jsonl",
        [{"id": cid, **e} for cid, e in gold_judge.items()],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="number of cases")
    parser.add_argument("--ids", default=None, help="comma-separated case ids")
    parser.add_argument(
        "--relabel", action="store_true", help="re-ask already-labeled items"
    )
    parser.add_argument("--captured", type=Path, default=CAPTURED_PATH)
    args = parser.parse_args()

    captured = _load_jsonl(args.captured)
    if not captured:
        print(f"No captured data at {args.captured}. Run eval_capture first.")
        return 1

    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",")}
        cases = [c for c in captured if c["id"] in wanted]
    else:
        cases = captured[: args.limit]

    gold_ret = {
        r["id"]: {lbl["doc_id"]: bool(lbl["relevant"]) for lbl in r.get("labels", [])}
        for r in _load_jsonl(GOLD_DIR / "retrieval.jsonl")
    }
    gold_resp = {
        r["id"]: {k: v for k, v in r.items() if k != "id"}
        for r in _load_jsonl(GOLD_DIR / "response.jsonl")
    }
    gold_judge = {
        r["id"]: {k: v for k, v in r.items() if k != "id"}
        for r in _load_jsonl(GOLD_DIR / "judge_calibration.jsonl")
    }

    print(
        f"Labeling {len(cases)} case(s). "
        "For each doc: y=relevant, n=not, s=skip, q=save & quit."
    )
    done = 0
    try:
        for cap in cases:
            label_case(cap, gold_ret, gold_resp, gold_judge, args.relabel)
            _save(gold_ret, gold_resp, gold_judge)
            done += 1
            print(f"  saved {cap['id']} ({done}/{len(cases)})")
    except QuitLabeling:
        print("\nQuitting — progress saved.")
    finally:
        _save(gold_ret, gold_resp, gold_judge)

    print(f"\nLabeled {done} case(s). Gold files updated in {GOLD_DIR}.")
    print("Next: python -m scripts.eval_score")
    return 0


if __name__ == "__main__":
    sys.exit(main())
