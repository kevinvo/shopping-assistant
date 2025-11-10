#!/usr/bin/env python3
import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


@dataclass
class ColdStartMetrics:
    is_cold_start: bool
    init_duration_ms: Optional[float] = None
    lambda_init_duration_ms: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "ColdStartMetrics":
        return cls(
            is_cold_start=data.get("is_cold_start", False),
            init_duration_ms=data.get("init_duration_ms"),
            lambda_init_duration_ms=data.get("lambda_init_duration_ms"),
        )


@dataclass
class ReportData:
    request_id: str
    lambda_init_duration_ms: Optional[float] = None
    duration_ms: Optional[float] = None
    timestamp: Optional[float] = None


@dataclass
class InvocationRecord:
    timestamp: Optional[float]
    metrics: ColdStartMetrics
    request_id: Optional[str] = None


@dataclass
class DurationStats:
    min_ms: float
    max_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float

    @classmethod
    def from_durations(cls, durations: List[float]) -> "DurationStats":
        sorted_durations = sorted(durations)
        return cls(
            min_ms=min(durations),
            max_ms=max(durations),
            avg_ms=sum(durations) / len(durations),
            p50_ms=sorted_durations[len(sorted_durations) // 2],
            p95_ms=sorted_durations[int(len(sorted_durations) * 0.95)],
            p99_ms=sorted_durations[int(len(sorted_durations) * 0.99)],
        )


@dataclass
class AnalysisStats:
    total_cold_starts: int
    total_warm_starts: int
    total_invocations: int
    cold_start_rate: float
    init_duration: Optional[DurationStats] = None
    lambda_init_duration: Optional[DurationStats] = None


@dataclass
class AnalysisResult:
    stats: AnalysisStats
    cold_starts: List[InvocationRecord]
    warm_starts: List[InvocationRecord]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--function-name",
        help="Lambda function name (e.g., shopping-assistant-api-chalice-test-scraper_worker)",
    )
    parser.add_argument(
        "--handler",
        choices=[
            "scraper_worker",
            "websocket_connect",
            "websocket_disconnect",
            "websocket_message",
        ],
        help="Handler name (will construct function name automatically)",
    )
    parser.add_argument(
        "--stage",
        default="chalice-test",
        help="Chalice stage (default: chalice-test)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Number of hours to look back (default: 24)",
    )
    parser.add_argument(
        "--region",
        default="ap-southeast-1",
        help="AWS region (default: ap-southeast-1)",
    )
    parser.add_argument(
        "--output",
        choices=["table", "json", "summary"],
        default="table",
        help="Output format (default: table)",
    )
    return parser.parse_args()


def get_log_group_name(function_name: str) -> str:
    return f"/aws/lambda/{function_name}"


def build_query(hours: int) -> str:
    return """
fields @timestamp, @message, @logStream
| filter @message like /COLD_START/
| sort @timestamp desc
| limit 1000
"""


def build_report_query(hours: int) -> str:
    return """
fields @timestamp, @message, @logStream
| filter @message like /REPORT/
| sort @timestamp desc
| limit 1000
"""


def execute_query(
    log_group: str, query: str, start_time: datetime, end_time: datetime, region: str
) -> List[Dict]:
    logs_client = boto3.client("logs", region_name=region)

    try:
        response = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
        )

        query_id = response["queryId"]

        while True:
            result = logs_client.get_query_results(queryId=query_id)
            status = result["status"]

            if status == "Complete":
                return result["results"]
            elif status == "Failed":
                raise RuntimeError(f"Query failed: {result.get('statistics', {})}")
            elif status == "Cancelled":
                raise RuntimeError("Query was cancelled")

            time.sleep(1)

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(
                f"Error: Log group '{log_group}' not found. "
                f"Make sure the function name is correct and logs exist.",
                file=sys.stderr,
            )
        else:
            print(f"Error querying logs: {e}", file=sys.stderr)
        raise


def parse_metrics_from_message(message: str) -> Optional[ColdStartMetrics]:
    try:
        if "cold_start_metrics" in message:
            start_idx = message.find('"cold_start_metrics": "')
            if start_idx != -1:
                start_idx += len('"cold_start_metrics": "')
                end_idx = message.find('"', start_idx)
                if end_idx != -1:
                    json_str = message[start_idx:end_idx].replace("\\", "")
                    data = json.loads(json_str)
                    return ColdStartMetrics.from_dict(data)
    except (json.JSONDecodeError, ValueError):
        pass

    is_cold_start = None
    init_duration_ms = None

    if "🚀 Cold start detected" in message:
        is_cold_start = True
    elif "⚡ Warm start" in message:
        is_cold_start = False

    if "Init:" in message:
        match = re.search(r"Init:\s*([0-9.]+)ms", message)
        if match:
            init_duration_ms = float(match.group(1))

    if is_cold_start is not None:
        return ColdStartMetrics(
            is_cold_start=is_cold_start,
            init_duration_ms=init_duration_ms,
        )

    return None


def parse_report_log(message: str) -> Optional[ReportData]:
    """Parse Lambda REPORT log to extract Init Duration and Request ID."""
    report_data = {}

    request_id_match = re.search(r"RequestId:\s*([a-f0-9-]+)", message)
    if not request_id_match:
        return None

    report_data["request_id"] = request_id_match.group(1)

    init_duration_match = re.search(r"Init Duration:\s*([0-9.]+)\s*ms", message)
    if init_duration_match:
        report_data["lambda_init_duration_ms"] = float(init_duration_match.group(1))

    duration_match = re.search(r"Duration:\s*([0-9.]+)\s*ms", message)
    if duration_match:
        report_data["duration_ms"] = float(duration_match.group(1))

    return ReportData(**report_data)


def extract_timestamp(row: List[Dict]) -> Optional[float]:
    """Extract timestamp from a CloudWatch Logs Insights row."""
    for field in row:
        if isinstance(field, dict) and field.get("field") == "@timestamp":
            ts_value = field.get("value")
            if ts_value:
                try:
                    return int(ts_value) / 1000
                except (ValueError, TypeError):
                    try:
                        dt = datetime.fromisoformat(
                            str(ts_value).replace("Z", "+00:00")
                        )
                        return dt.timestamp()
                    except (ValueError, AttributeError):
                        return None
    return None


def analyze_results(
    results: List[Dict], report_results: Optional[List[Dict]] = None
) -> AnalysisResult:
    cold_starts: List[InvocationRecord] = []
    warm_starts: List[InvocationRecord] = []
    all_init_durations: List[float] = []
    all_lambda_init_durations: List[float] = []

    report_map: Dict[str, ReportData] = {}
    if report_results:
        for row in report_results:
            message = ""
            timestamp = None
            for field in row:
                if field["field"] == "@message":
                    message = field["value"]
                elif field["field"] == "@timestamp":
                    timestamp = extract_timestamp(row)  # type: ignore

            report_data = parse_report_log(message)
            if report_data:
                report_data.timestamp = timestamp
                report_map[report_data.request_id] = report_data

    for row in results:
        message = ""
        timestamp = None
        request_id = None

        for field in row:
            if field["field"] == "@message":
                message = field["value"]
            elif field["field"] == "@timestamp":
                timestamp = extract_timestamp(row)  # type: ignore

        metrics = parse_metrics_from_message(message)
        if not metrics:
            continue

        request_id_match = re.search(r"RequestId:\s*([a-f0-9-]+)", message)
        if request_id_match:
            request_id = request_id_match.group(1)
            if request_id in report_map:
                report_data = report_map[request_id]
                if report_data.lambda_init_duration_ms is not None:
                    metrics.lambda_init_duration_ms = (
                        report_data.lambda_init_duration_ms
                    )

        record = InvocationRecord(
            timestamp=timestamp, metrics=metrics, request_id=request_id
        )

        if metrics.is_cold_start:
            cold_starts.append(record)
            if metrics.init_duration_ms is not None:
                all_init_durations.append(metrics.init_duration_ms)
            if metrics.lambda_init_duration_ms is not None:
                all_lambda_init_durations.append(metrics.lambda_init_duration_ms)
        else:
            warm_starts.append(record)

    total_invocations = len(cold_starts) + len(warm_starts)
    stats = AnalysisStats(
        total_cold_starts=len(cold_starts),
        total_warm_starts=len(warm_starts),
        total_invocations=total_invocations,
        cold_start_rate=(
            len(cold_starts) / total_invocations if total_invocations > 0 else 0.0
        ),
        init_duration=(
            DurationStats.from_durations(all_init_durations)
            if all_init_durations
            else None
        ),
        lambda_init_duration=(
            DurationStats.from_durations(all_lambda_init_durations)
            if all_lambda_init_durations
            else None
        ),
    )

    return AnalysisResult(stats=stats, cold_starts=cold_starts, warm_starts=warm_starts)


def print_table(analysis: AnalysisResult):
    stats = analysis.stats

    print("\n" + "=" * 80)
    print("COLD START ANALYSIS")
    print("=" * 80)
    print(f"\nTotal Invocations: {stats.total_invocations}")
    print(f"Cold Starts: {stats.total_cold_starts}")
    print(f"Warm Starts: {stats.total_warm_starts}")
    print(f"Cold Start Rate: {stats.cold_start_rate*100:.2f}%")

    if stats.init_duration:
        init = stats.init_duration
        print("\n" + "-" * 80)
        print("Python Module Init Duration (ms) - Custom Measurement")
        print("-" * 80)
        print(f"  Min:    {init.min_ms:.2f}")
        print(f"  Max:    {init.max_ms:.2f}")
        print(f"  Avg:    {init.avg_ms:.2f}")
        print(f"  P50:    {init.p50_ms:.2f}")
        print(f"  P95:    {init.p95_ms:.2f}")
        print(f"  P99:    {init.p99_ms:.2f}")

    if stats.lambda_init_duration:
        lambda_init = stats.lambda_init_duration
        print("\n" + "-" * 80)
        print("Lambda Init Duration (ms) - Full Cold Start (includes layer extraction)")
        print("-" * 80)
        print(f"  Min:    {lambda_init.min_ms:.2f}")
        print(f"  Max:    {lambda_init.max_ms:.2f}")
        print(f"  Avg:    {lambda_init.avg_ms:.2f}")
        print(f"  P50:    {lambda_init.p50_ms:.2f}")
        print(f"  P95:    {lambda_init.p95_ms:.2f}")
        print(f"  P99:    {lambda_init.p99_ms:.2f}")

    print("\n" + "=" * 80)

    if analysis.cold_starts:
        print("\nRecent Cold Starts:")
        print("-" * 80)
        for cs in analysis.cold_starts[:10]:
            ts = datetime.fromtimestamp(cs.timestamp) if cs.timestamp else "N/A"
            init_ms = cs.metrics.init_duration_ms or "N/A"
            lambda_init_ms = cs.metrics.lambda_init_duration_ms or "N/A"
            if lambda_init_ms != "N/A":
                print(
                    f"  {ts} | Python Init: {init_ms}ms | Lambda Init: {lambda_init_ms}ms"
                )
            else:
                print(f"  {ts} | Python Init: {init_ms}ms")


def print_summary(analysis: AnalysisResult):
    stats = analysis.stats

    print(
        f"Cold Starts: {stats.total_cold_starts}/{stats.total_invocations} "
        f"({stats.cold_start_rate*100:.1f}%)"
    )

    if stats.init_duration:
        init = stats.init_duration
        print(
            f"Python Init - Avg: {init.avg_ms:.2f}ms, "
            f"P95: {init.p95_ms:.2f}ms, P99: {init.p99_ms:.2f}ms"
        )

    if stats.lambda_init_duration:
        lambda_init = stats.lambda_init_duration
        print(
            f"Lambda Init - Avg: {lambda_init.avg_ms:.2f}ms, "
            f"P95: {lambda_init.p95_ms:.2f}ms, P99: {lambda_init.p99_ms:.2f}ms"
        )


def main():
    args = parse_args()

    if args.handler:
        function_name = f"shopping-assistant-api-{args.stage}-{args.handler}"
    elif args.function_name:
        function_name = args.function_name
    else:
        print(
            "Error: Either --function-name or --handler must be provided",
            file=sys.stderr,
        )
        sys.exit(1)

    log_group = get_log_group_name(function_name)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=args.hours)

    print(f"Analyzing cold starts for: {function_name}")
    print(f"Log group: {log_group}")
    print(f"Time range: {start_time} to {end_time}")
    print("Querying CloudWatch Logs...")

    query = build_query(args.hours)
    results = execute_query(log_group, query, start_time, end_time, args.region)

    if not results:
        print("\nNo cold start metrics found in the specified time range.")
        print(f"Query returned {len(results) if results else 0} results.")
        return

    print("Querying REPORT logs for Lambda Init Duration...")
    report_query = build_report_query(args.hours)
    report_results = execute_query(
        log_group, report_query, start_time, end_time, args.region
    )

    analysis = analyze_results(results, report_results)

    if args.output == "json":

        def json_serializer(obj):
            if isinstance(obj, (datetime, timedelta)):
                return str(obj)
            if hasattr(obj, "__dict__"):
                return (
                    asdict(obj)
                    if hasattr(obj, "__dataclass_fields__")
                    else obj.__dict__
                )
            return str(obj)

        print(json.dumps(asdict(analysis), indent=2, default=json_serializer))
    elif args.output == "summary":
        print_summary(analysis)
    else:
        print_table(analysis)


if __name__ == "__main__":
    main()
