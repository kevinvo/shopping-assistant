#!/usr/bin/env python3
"""Post-deployment helper to attach the shared layer to Chalice functions."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List

import boto3
from botocore.exceptions import ClientError

# Allow importing sibling script
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from attach_layer_to_functions import (  # noqa: E402
    LayerAttachError,
    attach_layer_to_functions,
    list_chalice_functions,
)

DEFAULT_STAGE = "chalice-test"
DEFAULT_REGION = "ap-southeast-1"
DEFAULT_LAYER_NAME = "shopping-assistant-chalice-layer"
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 5


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


class PostDeployError(RuntimeError):
    """Raised when post-deploy layer operations fail."""


def fetch_latest_layer_arn(layer_name: str, region: str) -> str:
    client = boto3.client("lambda", region_name=region)
    try:
        response = client.list_layer_versions(
            LayerName=layer_name,
            MaxItems=1,
        )
    except ClientError as exc:
        raise PostDeployError(
            f"Failed to list layer versions for {layer_name}: {exc.response.get('Error', {}).get('Message', exc)}"
        ) from exc

    versions = response.get("LayerVersions", [])
    if not versions:
        raise PostDeployError(
            f"No versions found for layer '{layer_name}' in region '{region}'"
        )

    return versions[0]["LayerVersionArn"]


def verify_layer_attachment(stage: str, region: str, layer_arn: str) -> bool:
    lambda_client = boto3.client("lambda", region_name=region)
    function_names = list_chalice_functions(stage, lambda_client)
    if not function_names:
        raise PostDeployError(
            f"No Lambda functions found for stage '{stage}' when verifying layer"
        )

    target_layer_name = layer_arn.split(":")[-2]

    missing: List[str] = []
    for function_name in function_names:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        layers = [layer.get("Arn", "") for layer in response.get("Layers", [])]
        if not any(target_layer_name in layer for layer in layers):
            missing.append(function_name)

    if missing:
        log(
            "WARN",
            f"Layer missing from {len(missing)} function(s): {', '.join(missing)}",
        )
        return False

    return True


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach and verify Lambda layer after Chalice deploy"
    )
    parser.add_argument("--stage", default=DEFAULT_STAGE, help="Chalice stage name")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument(
        "--layer-name",
        default=DEFAULT_LAYER_NAME,
        help="Name of the Lambda layer to attach",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Number of attachment retries",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=DEFAULT_RETRY_DELAY,
        help="Seconds to wait between retries",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    try:
        layer_arn = fetch_latest_layer_arn(args.layer_name, args.region)
    except PostDeployError as exc:
        log("ERROR", str(exc))
        return 1

    log(
        "INFO", "======================================================================"
    )
    log("INFO", "Post-Deployment Layer Attachment")
    log(
        "INFO", "======================================================================"
    )
    log("INFO", f"Stage: {args.stage}")
    log("INFO", f"Region: {args.region}")
    log("INFO", f"Layer Name: {args.layer_name}")
    log("INFO", f"Layer ARN: {layer_arn}")

    attempt = 0
    while attempt < args.max_retries:
        attempt += 1
        log("INFO", f"Attempt {attempt} of {args.max_retries}")
        try:
            attach_layer_to_functions(
                stage=args.stage, region=args.region, layer_arn=layer_arn
            )
        except LayerAttachError as exc:
            log("WARN", f"Layer attachment failed: {exc}")
        else:
            if verify_layer_attachment(args.stage, args.region, layer_arn):
                log("INFO", "✅ Post-deployment layer attachment complete!")
                return 0
            log("WARN", "Layer verification failed; retrying")

        if attempt < args.max_retries:
            log("INFO", f"Waiting {args.retry_delay} seconds before retry...")
            time.sleep(args.retry_delay)

    log("ERROR", "Failed to attach layer after retries")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
