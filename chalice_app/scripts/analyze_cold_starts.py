#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--function-name",
        required=True,
        help="Lambda function name (e.g., shopping-assistant-api-chalice-test-scraper_worker)",
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
| filter @message like /COLD_START_METRICS|🚀|⚡/
| parse @message /.*COLD_START_METRICS.*cold_start_metrics":\\s*"([^"]+)".*/ as metrics_json
| parse @message /.*🚀 Cold start detected for ([^|]+).*/ as handler_name_cold
| parse @message /.*⚡ Warm start for ([^|]+).*/ as handler_name_warm
| parse @message /.*Init: ([0-9.]+)ms.*/ as init_duration_ms
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
        import time

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


def parse_metrics_from_message(message: str) -> Optional[Dict]:
    try:
        if "cold_start_metrics" in message:
            start_idx = message.find('"cold_start_metrics": "')
            if start_idx != -1:
                start_idx += len('"cold_start_metrics": "')
                end_idx = message.find('"', start_idx)
                if end_idx != -1:
                    json_str = message[start_idx:end_idx].replace("\\", "")
                    return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        pass

    metrics = {}
    if "🚀 Cold start detected" in message:
        metrics["is_cold_start"] = True
    elif "⚡ Warm start" in message:
        metrics["is_cold_start"] = False

    if "Init:" in message:
        import re

        match = re.search(r"Init:\s*([0-9.]+)ms", message)
        if match:
            metrics["init_duration_ms"] = float(match.group(1))

    return metrics if metrics else None


def analyze_results(results: List[Dict]) -> Dict:
    cold_starts = []
    warm_starts = []
    all_init_durations = []

    for row in results:
        message = ""
        timestamp = None

        for field in row:
            if field["field"] == "@message":
                message = field["value"]
            elif field["field"] == "@timestamp":
                timestamp = int(field["value"]) / 1000

        metrics = parse_metrics_from_message(message)
        if not metrics:
            continue

        if metrics.get("is_cold_start"):
            cold_starts.append({"timestamp": timestamp, "metrics": metrics})
            if "init_duration_ms" in metrics:
                all_init_durations.append(metrics["init_duration_ms"])
        else:
            warm_starts.append({"timestamp": timestamp, "metrics": metrics})

    stats = {
        "total_cold_starts": len(cold_starts),
        "total_warm_starts": len(warm_starts),
        "total_invocations": len(cold_starts) + len(warm_starts),
        "cold_start_rate": (
            len(cold_starts) / (len(cold_starts) + len(warm_starts))
            if (len(cold_starts) + len(warm_starts)) > 0
            else 0
        ),
    }

    if all_init_durations:
        stats["init_duration"] = {
            "min_ms": min(all_init_durations),
            "max_ms": max(all_init_durations),
            "avg_ms": sum(all_init_durations) / len(all_init_durations),
            "p50_ms": sorted(all_init_durations)[len(all_init_durations) // 2],
            "p95_ms": sorted(all_init_durations)[int(len(all_init_durations) * 0.95)],
            "p99_ms": sorted(all_init_durations)[int(len(all_init_durations) * 0.99)],
        }

    return {
        "stats": stats,
        "cold_starts": cold_starts,
        "warm_starts": warm_starts,
    }


def print_table(analysis: Dict):
    stats = analysis["stats"]

    print("\n" + "=" * 80)
    print("COLD START ANALYSIS")
    print("=" * 80)
    print(f"\nTotal Invocations: {stats['total_invocations']}")
    print(f"Cold Starts: {stats['total_cold_starts']}")
    print(f"Warm Starts: {stats['total_warm_starts']}")
    print(f"Cold Start Rate: {stats['cold_start_rate']*100:.2f}%")

    if "init_duration" in stats:
        init = stats["init_duration"]
        print("\n" + "-" * 80)
        print("Initialization Duration (ms)")
        print("-" * 80)
        print(f"  Min:    {init['min_ms']:.2f}")
        print(f"  Max:    {init['max_ms']:.2f}")
        print(f"  Avg:    {init['avg_ms']:.2f}")
        print(f"  P50:    {init['p50_ms']:.2f}")
        print(f"  P95:    {init['p95_ms']:.2f}")
        print(f"  P99:    {init['p99_ms']:.2f}")

    print("\n" + "=" * 80)

    if analysis["cold_starts"]:
        print("\nRecent Cold Starts:")
        print("-" * 80)
        for cs in analysis["cold_starts"][:10]:
            ts = datetime.fromtimestamp(cs["timestamp"])
            init_ms = cs["metrics"].get("init_duration_ms", "N/A")
            print(f"  {ts} | Init: {init_ms}ms")


def print_summary(analysis: Dict):
    stats = analysis["stats"]

    print(
        f"Cold Starts: {stats['total_cold_starts']}/{stats['total_invocations']} "
        f"({stats['cold_start_rate']*100:.1f}%)"
    )

    if "init_duration" in stats:
        init = stats["init_duration"]
        print(
            f"Init Duration - Avg: {init['avg_ms']:.2f}ms, "
            f"P95: {init['p95_ms']:.2f}ms, P99: {init['p99_ms']:.2f}ms"
        )


def main():
    args = parse_args()
    log_group = get_log_group_name(args.function_name)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=args.hours)

    print(f"Analyzing cold starts for: {args.function_name}")
    print(f"Log group: {log_group}")
    print(f"Time range: {start_time} to {end_time}")
    print("Querying CloudWatch Logs...")

    query = build_query(args.hours)
    results = execute_query(log_group, query, start_time, end_time, args.region)

    if not results:
        print("\nNo cold start metrics found in the specified time range.")
        return

    analysis = analyze_results(results)

    if args.output == "json":
        print(json.dumps(analysis, indent=2, default=str))
    elif args.output == "summary":
        print_summary(analysis)
    else:
        print_table(analysis)


if __name__ == "__main__":
    main()
