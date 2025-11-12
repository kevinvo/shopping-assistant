#!/usr/bin/env python3
"""Update all Lambda function runtimes to Python 3.12.

Chalice 1.32.0 doesn't respect the 'runtime' field in config.json, so we need
to update runtimes manually after each deployment.
"""

from __future__ import annotations

import argparse
import sys
from typing import List

import boto3
from botocore.exceptions import ClientError

DEFAULT_STAGE = "chalice-test"
DEFAULT_REGION = "ap-southeast-1"
TARGET_RUNTIME = "python3.12"


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


class RuntimeUpdateError(RuntimeError):
    """Raised when runtime update fails."""


def list_chalice_functions(stage: str, lambda_client) -> List[str]:
    """List all Lambda functions for the given stage."""
    prefix = f"shopping-assistant-api-{stage}"
    functions: List[str] = []
    paginator = lambda_client.get_paginator("list_functions")

    for page in paginator.paginate():
        for function in page.get("Functions", []):
            name = function.get("FunctionName", "")
            if name.startswith(prefix):
                functions.append(name)

    return sorted(functions)


def update_function_runtime(
    function_name: str, lambda_client, target_runtime: str
) -> bool:
    """Update a function's runtime. Returns True if updated, False if already correct."""
    try:
        # Get current runtime
        config = lambda_client.get_function_configuration(FunctionName=function_name)
        current_runtime = config.get("Runtime", "")

        if current_runtime == target_runtime:
            log("INFO", f"  {function_name}: Already {target_runtime}")
            return False

        # Update runtime
        lambda_client.update_function_configuration(
            FunctionName=function_name, Runtime=target_runtime
        )
        log(
            "INFO",
            f"  {function_name}: Updated from {current_runtime} to {target_runtime}",
        )
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            log("WARN", f"  {function_name}: Function not found")
            return False
        else:
            log("ERROR", f"  {function_name}: Failed to update - {e}")
            raise RuntimeUpdateError(f"Failed to update {function_name}: {e}") from e


def update_runtimes(
    stage: str, region: str, target_runtime: str = TARGET_RUNTIME
) -> None:
    """Update all function runtimes for the stage."""
    lambda_client = boto3.client("lambda", region_name=region)

    log("INFO", f"Updating Lambda function runtimes to {target_runtime}")
    log("INFO", f"Stage: {stage} | Region: {region}")
    log("INFO", "")

    # List all functions
    functions = list_chalice_functions(stage, lambda_client)

    if not functions:
        log("INFO", "No functions found for this stage")
        return

    log("INFO", f"Found {len(functions)} function(s)")
    log("INFO", "")

    updated_count = 0
    already_correct = 0
    failed_count = 0

    for function_name in functions:
        try:
            if update_function_runtime(function_name, lambda_client, target_runtime):
                updated_count += 1
            else:
                already_correct += 1
        except RuntimeUpdateError:
            failed_count += 1

    log("INFO", "")
    log("INFO", "=" * 70)
    log(
        "INFO",
        f"Runtime update complete: {updated_count} updated, {already_correct} already correct, {failed_count} failed",
    )
    log("INFO", "=" * 70)

    if failed_count > 0:
        raise RuntimeUpdateError(f"Failed to update {failed_count} function(s)")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update Lambda function runtimes to Python 3.12"
    )
    parser.add_argument(
        "--stage",
        default=DEFAULT_STAGE,
        help=f"Chalice stage name (default: {DEFAULT_STAGE})",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--runtime",
        default=TARGET_RUNTIME,
        help=f"Target runtime (default: {TARGET_RUNTIME})",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    try:
        update_runtimes(
            stage=args.stage,
            region=args.region,
            target_runtime=args.runtime,
        )
    except RuntimeUpdateError as exc:
        log("ERROR", str(exc))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
