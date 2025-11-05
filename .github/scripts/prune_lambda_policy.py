#!/usr/bin/env python3
"""Utility to prune Lambda permission statements before Chalice deploy."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Iterable

import boto3


def iter_statement_ids(policy: str) -> Iterable[str]:
    data = json.loads(policy)
    for statement in data.get("Statement", []):
        sid = statement.get("Sid")
        if sid:
            yield sid


def prune_policy(
    function_name: str, region: str, max_attempts: int = 5, delay_seconds: float = 3.0
) -> None:
    client = boto3.client("lambda", region_name=region)

    for attempt in range(1, max_attempts + 1):
        try:
            policy = client.get_policy(FunctionName=function_name)["Policy"]
        except client.exceptions.ResourceNotFoundException:
            print(f"No resource policy present on {function_name}")
            return

        sids = list(iter_statement_ids(policy))
        if not sids:
            print(f"Resource policy already empty for {function_name}")
            return

        print(f"Removing {len(sids)} statement(s) from {function_name}")
        for sid in sids:
            try:
                client.remove_permission(FunctionName=function_name, StatementId=sid)
                print(f"  - removed {sid}")
            except client.exceptions.ResourceNotFoundException:
                # Another process may have removed it already.
                pass

        if attempt == max_attempts:
            break

        print("Waiting for policy updates to propagate...")
        time.sleep(delay_seconds)

    # Final check
    try:
        remaining = list(
            iter_statement_ids(client.get_policy(FunctionName=function_name)["Policy"])
        )
    except client.exceptions.ResourceNotFoundException:
        remaining = []

    if remaining:
        raise RuntimeError(
            f"Failed to prune {len(remaining)} policy statement(s) from {function_name}: {', '.join(remaining)}"
        )

    print(f"Lambda resource policy pruned for {function_name}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        default="chalice-test",
        help="Chalice stage name (default: %(default)s)",
    )
    parser.add_argument(
        "--function",
        help="Fully qualified Lambda function name. Overrides --stage if supplied.",
    )
    parser.add_argument(
        "--region", default="ap-southeast-1", help="AWS region (default: %(default)s)"
    )

    args = parser.parse_args(argv)

    function_name = args.function or f"shopping-assistant-api-{args.stage}"

    try:
        prune_policy(function_name=function_name, region=args.region)
    except Exception as exc:  # pragma: no cover - defensive for CI usage
        print(f"::error::Failed to prune Lambda policy: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
