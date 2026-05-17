"""Replay a fixed query set through the deployed chat pipeline + evaluator,
dump scores. Use this as a baseline before changing a prompt; re-run after
the change and diff to know whether the change was an improvement.

Per-query flow:
  WebSocket message -> chat_processor Lambda -> LangSmith trace -> async
  evaluator Lambda -> LangSmith feedback (faithfulness / actionability /
  retrieval_relevance).

This script tags each session_id with a label, drives the WS, then polls
LangSmith for the resulting feedback and aggregates by metric.

Run:
    cd chalice_app
    python -m scripts.run_eval_baseline

Outputs:
    eval_results/<timestamp>/queries.csv    -- per-query session ids
    eval_results/<timestamp>/scores.csv     -- per-query metric scores
    stdout                                   -- mean and median per metric
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple, Union

import boto3
import websockets
from langsmith import Client

from chalicelib.core.config import config

WS_STAGE = "chalice-test"
WS_API_NAME_FRAGMENT = "chalice-test-websocket-api"
LANGSMITH_PROJECT = "shopping-assistant-test"
REGION = "ap-southeast-1"

# Each entry is either a single user message (single-turn) or a tuple of
# (setup_user_message, shift_user_message) for a 2-turn topic-shift test.
Query = Union[str, Tuple[str, str]]

QUERIES: List[Query] = [
    # --- Direct product queries (single-turn) ---
    "best running shoes for flat feet",
    "good insulated travel mug recommendations",
    "indoor plants for low light apartment",
    "durable cookware brands for everyday use",
    "best wireless headphones with noise cancellation under $300",
    "recommend a backpack for daily commute under $100",
    "what coffee maker should I get for a small office",
    "best ergonomic office chair under $500",
    "best chef's knife under $200",
    "best electric kettle for everyday tea",
    # --- Comparisons ---
    "All-Clad vs Le Creuset for stainless cookware",
    "Sony WH-1000XM5 vs Bose QuietComfort 45",
    "MacBook Air vs ThinkPad for college",
    # --- Vague / clarifying-question expected ---
    "I need a gift",
    "I want to start cooking more",
    # --- Topic-shift second turns (paired) ---
    ("birthday gift ideas for my girlfriend", "tell me about backpacks"),
    ("best running shoes for flat feet", "i need a new laptop for college"),
    ("how to clean a cast iron pan", "what coffee makers do you recommend"),
    # --- Niche / corpus-edge ---
    "BIFL kitchen scale recommendation",
    "best men's winter coat under $300",
    "good board games for adults",
    "best vacuum for pet hair",
    "winter tires for daily commute in cold climate",
    "good shovel for clay soil",
    "best gaming laptop under $1500",
]

CHUNK_TIMEOUT_SECONDS = 90
FEEDBACK_POLL_ATTEMPTS = 15
FEEDBACK_POLL_INTERVAL_SECONDS = 4


def _label(item: Query) -> str:
    if isinstance(item, tuple):
        return f"shift::{item[0][:25]}->{item[1][:35]}"
    return item[:60]


async def _send_turns(ws, session_id, conversation_id, turns: List[str]) -> str:
    """Send `turns` sequentially, return session_id (the last turn is the
    one we want scored)."""
    for content in turns:
        await ws.send(
            json.dumps(
                {
                    "type": "message",
                    "sessionId": session_id,
                    "conversationId": conversation_id,
                    "messageId": str(uuid.uuid4()),
                    "content": content,
                }
            )
        )
        while True:
            data = json.loads(await asyncio.wait_for(ws.recv(), CHUNK_TIMEOUT_SECONDS))
            if data.get("type") in ("message_end", "done"):
                break
    return session_id


async def _drive(ws_url_base: str) -> List[dict]:
    """Open one WS per query, send turns, capture session_id. Returns list of
    {label, session_id, query, started_at}."""
    rows = []
    for i, item in enumerate(QUERIES, 1):
        sid = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        url = f"{ws_url_base}?session_id={sid}"
        label = _label(item)
        turns = list(item) if isinstance(item, tuple) else [item]
        last_query = turns[-1]
        print(f"[{i}/{len(QUERIES)}] {label}")
        started_at = datetime.now(timezone.utc)
        try:
            async with websockets.connect(url) as ws:
                await _send_turns(ws, sid, cid, turns)
            rows.append(
                {
                    "label": label,
                    "session_id": sid,
                    "query": last_query,
                    "started_at_iso": started_at.isoformat(),
                }
            )
        except Exception as e:
            print(f"  FAILED: {e}")
            rows.append({"label": label, "session_id": sid, "error": str(e)})
    return rows


def _resolve_ws_url() -> str:
    apigw = boto3.client("apigatewayv2", region_name=REGION)
    for api in apigw.get_apis().get("Items", []):
        if WS_API_NAME_FRAGMENT in api.get("Name", ""):
            return f"{api['ApiEndpoint']}/{WS_STAGE}/"
    raise RuntimeError(f"could not find WS API matching {WS_API_NAME_FRAGMENT!r}")


def _collect_scores(rows: List[dict], window_start: datetime) -> List[dict]:
    """For each session_id, find its chat_session run and pull feedback
    posted by the evaluator Lambda."""
    c = Client(api_key=config.langsmith_api_key, api_url=config.langsmith_api_url)

    session_ids = {r["session_id"] for r in rows if r.get("session_id")}

    # Two-stage poll: first wait for chat runs to ingest, then for evaluator
    # feedback to land. Evaluator runs on SQS lag (~5-15 s after chat ends).
    session_to_runid: dict = {}
    for attempt in range(FEEDBACK_POLL_ATTEMPTS):
        runs = list(
            c.list_runs(
                project_name=LANGSMITH_PROJECT,
                start_time=window_start,
                run_type="chain",
                limit=500,
            )
        )
        for r in runs:
            if r.name != "chat_session":
                continue
            md = r.extra.get("metadata", {}) if r.extra else {}
            sid = md.get("session_id")
            if sid in session_ids and sid not in session_to_runid:
                session_to_runid[sid] = r.id
        missing = session_ids - set(session_to_runid)
        print(
            f"  found {len(session_to_runid)}/{len(session_ids)} chat runs"
            f" (attempt {attempt + 1})"
        )
        if not missing:
            break
        time.sleep(FEEDBACK_POLL_INTERVAL_SECONDS)

    # Now fetch feedback per run. Skip runs we never matched.
    score_rows = []
    for row in rows:
        sid = row.get("session_id")
        run_id = session_to_runid.get(sid)
        if not run_id:
            score_rows.append({**row, "_run_status": "no run found"})
            continue
        feedbacks = list(c.list_feedback(run_ids=[run_id]))
        scores = {fb.key: fb.score for fb in feedbacks if fb.score is not None}
        # Pull prompt versions from the run metadata for slicing later
        r = c.read_run(run_id)
        md = r.extra.get("metadata", {}) if r.extra else {}
        score_rows.append(
            {
                **row,
                "run_id": run_id,
                "persona_version": md.get("persona_version"),
                **scores,
                "_run_status": "ok",
                "_feedback_count": len(scores),
            }
        )
    return score_rows


def _summarize(score_rows: List[dict]) -> None:
    """Mean + median per metric across rows where the metric landed."""
    # The evaluator posts feedback keyed by metric name (e.g. faithfulness,
    # actionability, retrieval_relevance, plus the heuristic ones).
    skip_keys = {
        "label",
        "session_id",
        "query",
        "started_at_iso",
        "run_id",
        "persona_version",
        "error",
        "_run_status",
        "_feedback_count",
    }
    metric_keys: set = set()
    for r in score_rows:
        for k, v in r.items():
            if k in skip_keys:
                continue
            if isinstance(v, (int, float)):
                metric_keys.add(k)

    if not metric_keys:
        print("\n(no scored metrics found)")
        return

    print("\n=== Summary ===")
    print(f"{'metric':<28} {'mean':>8} {'median':>8} {'n':>4}")
    print("-" * 52)
    for key in sorted(metric_keys):
        vals = [r[key] for r in score_rows if isinstance(r.get(key), (int, float))]
        if not vals:
            continue
        m = statistics.mean(vals)
        med = statistics.median(vals)
        print(f"{key:<28} {m:>8.3f} {med:>8.3f} {len(vals):>4}")

    matched = sum(1 for r in score_rows if r.get("_run_status") == "ok")
    print(
        f"\nmatched {matched}/{len(score_rows)} runs;"
        f" total queries: {len(score_rows)}"
    )


def main() -> int:
    out_dir = (
        Path(__file__).resolve().parent.parent
        / "eval_results"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {out_dir}")

    ws_url = _resolve_ws_url()
    print(f"WS: {ws_url}\n")

    window_start = datetime.now(timezone.utc) - timedelta(minutes=1)
    rows = asyncio.run(_drive(ws_url))

    # Save which queries we ran first so the artifact exists even if score
    # collection fails downstream.
    queries_path = out_dir / "queries.csv"
    if rows:
        fields = sorted({k for r in rows for k in r.keys()})
        with queries_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    print(f"\nWrote {queries_path}")

    print("\nCollecting evaluator feedback from LangSmith...")
    score_rows = _collect_scores(rows, window_start)

    scores_path = out_dir / "scores.csv"
    if score_rows:
        fields = sorted({k for r in score_rows for k in r.keys()})
        with scores_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(score_rows)
    print(f"Wrote {scores_path}")

    _summarize(score_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
