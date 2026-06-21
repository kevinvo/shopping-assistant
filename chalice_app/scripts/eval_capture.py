"""Phase 1 of the static labeled eval set: run each frozen case through the
real RAG pipeline and capture what it retrieved + answered, so a human can
label it.

Unlike scripts/run_eval_baseline.py (which drives the deployed WebSocket and
polls LangSmith for the LLM-judge's aggregate scores), this calls
Chat.process_chat() DIRECTLY and reads the returned eval_metadata. That is the
only way to get the raw retrieved-document texts and their doc_ids, which are
not sent to the WS client and not reliably persisted to LangSmith. It makes
real calls to Qdrant + the embeddings/LLM APIs using your .env credentials.

Per case, the last turn is the one we capture (earlier turns just establish
conversation context, e.g. the topic-shift cases).

Run:
    cd chalice_app
    python -m scripts.eval_capture                 # all cases
    python -m scripts.eval_capture --limit 3       # quick smoke
    python -m scripts.eval_capture --source curated

Input:
    eval/dataset/cases.jsonl

Output:
    eval/captured/to_label.jsonl   -- one record per case, each with a `docs`
                                      list whose `relevant` fields are null and
                                      ready for a human to fill in.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from chalicelib.sessions.chat_session_manager import Chat

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
CASES_PATH = EVAL_DIR / "dataset" / "cases.jsonl"
DEFAULT_OUT_PATH = EVAL_DIR / "captured" / "to_label.jsonl"


def _load_cases(source: Optional[str], limit: Optional[int]) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    with CASES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            if source and case.get("source") != source:
                continue
            cases.append(case)
    if limit is not None:
        cases = cases[:limit]
    return cases


def _docs_for_labeling(eval_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Join reranker judgments (doc_id -> relevance_score) with the pre-rerank
    doc texts (doc_id -> text), best-ranked first, into a flat list the human
    annotates. `relevant` starts null."""
    text_by_id = {
        d["doc_id"]: d["text"] for d in eval_metadata.get("pre_rerank_results", [])
    }
    judgments = sorted(
        eval_metadata.get("reranker_scores", []),
        key=lambda j: j["relevance_score"],
        reverse=True,
    )
    docs = []
    for rank, j in enumerate(judgments, 1):
        docs.append(
            {
                "doc_id": j["doc_id"],
                "rank": rank,
                "reranker_score": j["relevance_score"],
                "text": text_by_id.get(j["doc_id"], ""),
                "relevant": None,
            }
        )
    return docs


def _capture_case(chat: Chat, case: Dict[str, Any]) -> Dict[str, Any]:
    """Drive every turn; capture the eval_metadata of the LAST turn."""
    turns: List[str] = case["turns"]
    session_id = str(uuid.uuid4())
    history: List[Dict[str, str]] = []
    response = ""
    eval_metadata: Dict[str, Any] = {}

    for turn in turns:
        response, _, eval_metadata = chat.process_chat(
            query=turn,
            session_id=session_id,
            chat_history=history,
            socket_id="eval-capture",
        )
        history = history + [
            {"role": "user", "content": turn},
            {"role": "assistant", "content": response},
        ]

    return {
        "id": case["id"],
        "source": case.get("source"),
        "tags": case.get("tags", []),
        "turns": turns,
        "query": turns[-1],
        "session_id": session_id,
        "run_id": eval_metadata.get("run_id"),
        "rewritten_query": eval_metadata.get("rewritten_query"),
        "hyde_query": eval_metadata.get("hyde_query"),
        "response": response,
        "docs": _docs_for_labeling(eval_metadata),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="cap number of cases")
    parser.add_argument(
        "--source", default=None, help="only cases with this source (e.g. curated)"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_PATH, help="output jsonl path"
    )
    args = parser.parse_args()

    cases = _load_cases(args.source, args.limit)
    if not cases:
        print("No cases matched.", file=sys.stderr)
        return 1
    print(f"Capturing {len(cases)} case(s) -> {args.out}")

    chat = Chat()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    captured = 0
    failed = 0
    with args.out.open("w") as out:
        for i, case in enumerate(cases, 1):
            label = case["id"]
            print(f"[{i}/{len(cases)}] {label}: {case['turns'][-1][:60]}")
            try:
                record = _capture_case(chat, case)
            except Exception as e:  # keep going; one bad case shouldn't abort
                print(f"  FAILED: {e}")
                failed += 1
                continue
            out.write(json.dumps(record) + "\n")
            out.flush()
            captured += 1
            print(f"  ok: {len(record['docs'])} docs, run_id={record['run_id']}")

    print(f"\nWrote {captured} record(s) to {args.out} ({failed} failed).")
    print("Next: label the `relevant` fields, then add gold/ entries.")
    return 0 if captured else 1


if __name__ == "__main__":
    sys.exit(main())
