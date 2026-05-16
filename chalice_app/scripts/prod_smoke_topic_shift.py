"""End-to-end battery of topic-shift smoke tests against the deployed env.

Each case is a 2-3 turn conversation that ends with a clean topic shift.
For the shift turn we check the deployed Lambda's CloudWatch logs and
verify both the Rewritten query and the HyDE output:

  * neither carries any keyword from the previously established topic
    ("bleed" — what we observed in the original bug report)
  * the HyDE mentions at least one on-topic keyword (sanity check that
    the output isn't generic mush)

Drives the real WebSocket (`chalice-test` stage — the project's
user-facing deployment) so we exercise the deployed prompts + pipeline,
not just direct LLM calls (that's what scripts/check_topic_shift.py does).

Run:
    cd chalice_app
    python -m scripts.prod_smoke_topic_shift

Exits 0 if all cases pass, non-zero with a per-case verdict otherwise.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Tuple

import boto3
import websockets

# The "chalice-prod" stage exists in .chalice/config.json but its
# WebSocket isn't deployed (WEBSOCKET_DOMAIN still INJECTED_BY_DEPLOY on
# the prod Lambda), so the user-facing deployment is `chalice-test`.
#
# API Gateway IDs can rotate on CDK redeploy. Look up the live endpoint
# at runtime instead of hardcoding so this script doesn't go stale.
WS_STAGE = "chalice-test"
LOG_GROUP = "/aws/lambda/shopping-assistant-api-chalice-test-chat_processor"
REGION = "ap-southeast-1"
WS_API_NAME_FRAGMENT = "chalice-test-websocket-api"

CHUNK_TIMEOUT_SECONDS = 90
LOG_POLL_ATTEMPTS = 10
LOG_POLL_INTERVAL_SECONDS = 3


@dataclass
class Case:
    name: str
    establish: List[str]
    shift: str
    bleed: Tuple[str, ...]
    on_topic: Tuple[str, ...]


CASES: List[Case] = [
    Case(
        name="gift -> backpack",
        establish=[
            "I want to buy a birthday's gift for my gf",
            "What about birthday cards?",
        ],
        shift="tell me about backpacks",
        bleed=(
            "jewelry",
            "keepsake",
            "spa",
            "candle",
            "perfume",
            "chocolate",
            "flowers",
            "gift box",
            "engraved",
            "sentimental",
        ),
        on_topic=("backpack",),
    ),
    Case(
        name="running shoes -> laptop",
        establish=[
            "best running shoes for flat feet",
            "what about for trail running?",
        ],
        shift="i need a new laptop for college",
        bleed=("running shoe", "sole", "arch support", "outsole", "trail"),
        on_topic=("laptop", "macbook", "thinkpad", "college", "notebook"),
    ),
    Case(
        name="cast iron -> coffee maker",
        establish=[
            "how to clean a cast iron pan",
            "can i use soap on it?",
        ],
        shift="what coffee makers do you recommend",
        bleed=("cast iron", "season", "skillet", "rust", "lard"),
        on_topic=(
            "coffee",
            "espresso",
            "brewer",
            "drip",
            "french press",
            "moka",
        ),
    ),
    Case(
        name="headphones -> houseplants",
        establish=[
            "best wireless headphones with noise cancellation",
            "which work well in an office?",
        ],
        shift="i want to buy some indoor plants",
        bleed=(
            "headphone",
            "noise cancel",
            "bluetooth",
            "earbud",
            "audio",
            "drivers",
        ),
        on_topic=(
            "plant",
            "indoor",
            "houseplant",
            "potted",
            "succulent",
            "pothos",
        ),
    ),
    Case(
        name="skincare -> commuter bike",
        establish=[
            "skincare routine for men",
            "what cleansers work for oily skin?",
        ],
        shift="tell me about commuter bikes",
        bleed=(
            "skincare",
            "cleanser",
            "moisturizer",
            "serum",
            "sunscreen",
            "spf",
        ),
        on_topic=("bike", "bicycle", "commuter", "cycling", "frame"),
    ),
    Case(
        name="gaming laptop -> cookware",
        establish=[
            "gaming laptops under 1000 dollars",
            "best GPU for that price range?",
        ],
        shift="what cookware brands are durable",
        bleed=("gpu", "rtx", "ryzen", "fps", "gaming laptop"),
        on_topic=(
            "cookware",
            "pot",
            "skillet",
            "saucepan",
            "le creuset",
            "all-clad",
            "stainless",
        ),
    ),
    Case(
        name="sci-fi novels -> groceries",
        establish=[
            "best sci-fi novels of the last decade",
            "any by women authors?",
        ],
        shift="where can i buy organic produce online",
        bleed=("novel", "fiction", "sci-fi", "author", "prose", "trilogy"),
        on_topic=(
            "organic",
            "produce",
            "grocery",
            "vegetable",
            "fruit",
            "delivery",
            "farm",
        ),
    ),
    Case(
        name="fitness tracker -> garden shovel",
        establish=[
            "fitness tracker for running",
            "does it track sleep too?",
        ],
        shift="i need a good shovel for my garden",
        bleed=("fitness tracker", "heart rate", "sleep tracking", "steps", "stride"),
        on_topic=("shovel", "garden", "soil", "spade", "digging", "yard"),
    ),
    Case(
        name="robot vacuum -> travel mug",
        establish=[
            "robot vacuum for homes with pets",
            "which are quietest?",
        ],
        shift="looking for a good insulated travel mug",
        bleed=("vacuum", "robot", "pet hair", "suction", "roomba", "filter"),
        on_topic=(
            "mug",
            "thermos",
            "tumbler",
            "insulated",
            "stainless steel",
            "yeti",
            "hydro flask",
        ),
    ),
    Case(
        name="OLED TV -> board games",
        establish=[
            "best 65 inch TVs under $1000",
            "what about OLED options?",
        ],
        shift="recommend board games for adults",
        bleed=("oled", "hdr", "refresh rate", "hdmi", "smart tv", "resolution"),
        on_topic=(
            "board game",
            "tabletop",
            "strategy game",
            "card game",
            "dice",
        ),
    ),
]


@dataclass
class CaseResult:
    case: Case
    shift_message_id: str
    shift_start_ms: int
    response_preview: str = ""
    rewrite_line: str = ""
    hyde_line: str = ""
    rewrite_bleed_hits: List[str] = field(default_factory=list)
    hyde_bleed_hits: List[str] = field(default_factory=list)
    hyde_on_topic_hit: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return (
            not self.rewrite_bleed_hits
            and not self.hyde_bleed_hits
            and self.hyde_on_topic_hit
        )


# ---------------------------------------------------------------------------
# WebSocket driver
# ---------------------------------------------------------------------------


async def _send_and_collect(ws, payload: dict) -> str:
    await ws.send(json.dumps(payload))
    chunks: List[str] = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=CHUNK_TIMEOUT_SECONDS)
        data = json.loads(raw)
        msg_type = data.get("type")
        if msg_type in ("message_chunk", "chunk"):
            chunks.append(data.get("content", ""))
        elif msg_type in ("message_end", "done"):
            return "".join(chunks)
        elif msg_type == "error":
            raise RuntimeError(f"server error: {data}")
        # ignore message_start, processing, pong, etc.


def _resolve_ws_url() -> str:
    apigw = boto3.client("apigatewayv2", region_name=REGION)
    apis = apigw.get_apis().get("Items", [])
    for api in apis:
        if WS_API_NAME_FRAGMENT in api.get("Name", ""):
            endpoint = api["ApiEndpoint"]  # e.g. wss://abc.execute-api...
            return f"{endpoint}/{WS_STAGE}/"
    raise RuntimeError(
        f"could not find WebSocket API with name containing "
        f"{WS_API_NAME_FRAGMENT!r}"
    )


async def _run_case(case: Case, ws_url_base: str) -> CaseResult:
    session_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    url = f"{ws_url_base}?session_id={session_id}"

    shift_message_id = str(uuid.uuid4())
    shift_start_ms = 0
    response_preview = ""

    try:
        async with websockets.connect(url) as ws:
            for turn_content in case.establish:
                payload = {
                    "type": "message",
                    "sessionId": session_id,
                    "conversationId": conversation_id,
                    "messageId": str(uuid.uuid4()),
                    "content": turn_content,
                }
                await _send_and_collect(ws, payload)

            # Shift turn — this is the one we verify.
            shift_start_ms = int(time.time() * 1000)
            payload = {
                "type": "message",
                "sessionId": session_id,
                "conversationId": conversation_id,
                "messageId": shift_message_id,
                "content": case.shift,
            }
            text = await _send_and_collect(ws, payload)
            response_preview = text[:180]
    except Exception as e:
        return CaseResult(
            case=case,
            shift_message_id=shift_message_id,
            shift_start_ms=shift_start_ms,
            error=f"WebSocket error: {e}",
        )

    return CaseResult(
        case=case,
        shift_message_id=shift_message_id,
        shift_start_ms=shift_start_ms,
        response_preview=response_preview,
    )


# ---------------------------------------------------------------------------
# CloudWatch lookup
# ---------------------------------------------------------------------------


def _poll_log_line(logs_client, start_ms: int, filter_pattern: str) -> str:
    """Poll CloudWatch for the most recent line matching filter_pattern
    in the window starting at start_ms. Returns the message text or a
    placeholder if nothing landed in the poll window."""
    for _ in range(LOG_POLL_ATTEMPTS):
        time.sleep(LOG_POLL_INTERVAL_SECONDS)
        try:
            resp = logs_client.filter_log_events(
                logGroupName=LOG_GROUP,
                startTime=start_ms,
                filterPattern=filter_pattern,
                limit=20,
            )
        except logs_client.exceptions.ResourceNotFoundException:
            return f"<no log group: {LOG_GROUP}>"
        events = resp.get("events", [])
        if events:
            return events[-1]["message"].strip()
    return "<no matching log event in poll window>"


def _enrich_with_logs(result: CaseResult) -> None:
    if result.error or result.shift_start_ms == 0:
        return

    logs = boto3.client("logs", region_name=REGION)
    result.rewrite_line = _poll_log_line(
        logs, result.shift_start_ms, '"Rewritten query"'
    )
    result.hyde_line = _poll_log_line(
        logs, result.shift_start_ms, '"HyDE response query"'
    )

    if result.rewrite_line.startswith("<"):
        result.error = f"rewrite log lookup failed: {result.rewrite_line}"
        return
    if result.hyde_line.startswith("<"):
        result.error = f"hyde log lookup failed: {result.hyde_line}"
        return

    rewrite_text = result.rewrite_line.split("Rewritten query:", 1)[-1].lower()
    hyde_text = result.hyde_line.split("HyDE response query:", 1)[-1].lower()

    result.rewrite_bleed_hits = [k for k in result.case.bleed if k in rewrite_text]
    result.hyde_bleed_hits = [k for k in result.case.bleed if k in hyde_text]
    result.hyde_on_topic_hit = any(k in hyde_text for k in result.case.on_topic)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_case_result(idx: int, result: CaseResult) -> None:
    verdict = "PASS" if result.passed else "FAIL"
    print(f"\n--- [{idx}/{len(CASES)}] {result.case.name} -- {verdict} ---")
    print(f"  Shift:    {result.case.shift!r}")
    print(f"  Rewrite:  {result.rewrite_line}")
    print(f"  HyDE:     {result.hyde_line}")
    if result.rewrite_bleed_hits:
        print(f"  Rewrite bleed: {result.rewrite_bleed_hits}")
    if result.hyde_bleed_hits:
        print(f"  HyDE bleed:    {result.hyde_bleed_hits}")
    if not result.hyde_on_topic_hit and not result.error:
        print(f"  HyDE missing on-topic keywords from: {result.case.on_topic}")
    if result.error:
        print(f"  Error: {result.error}")
    print(f"  Response preview: {result.response_preview!r}")


async def main_async() -> int:
    ws_url_base = _resolve_ws_url()
    print(f"Driving {len(CASES)} topic-shift cases against {ws_url_base}\n")

    results: List[CaseResult] = []
    for idx, case in enumerate(CASES, 1):
        print(f"[{idx}/{len(CASES)}] Running: {case.name}")
        result = await _run_case(case, ws_url_base)
        _enrich_with_logs(result)
        results.append(result)
        _print_case_result(idx, result)

    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    print("\n" + "=" * 60)
    print(f"SUMMARY: {len(passed)}/{len(results)} passed")
    print("=" * 60)
    if failed:
        for r in failed:
            print(f"  FAIL: {r.case.name}")
            if r.error:
                print(f"        {r.error}")
            elif r.rewrite_bleed_hits or r.hyde_bleed_hits:
                bleed = (r.rewrite_bleed_hits or []) + (r.hyde_bleed_hits or [])
                print(f"        bleed keywords: {bleed}")
            elif not r.hyde_on_topic_hit:
                print(
                    f"        HyDE generic (no on-topic keyword from {r.case.on_topic})"
                )

    return 0 if not failed else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
