#!/usr/bin/env python3
"""Attach the shared Lambda layer to all Chalice-managed functions."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import boto3
from botocore.exceptions import ClientError

DEFAULT_STAGE = "chalice-test"
DEFAULT_REGION = "ap-southeast-1"


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


class LayerAttachError(RuntimeError):
    """Raised when layer attachment fails."""


@dataclass
class FunctionLayers:
    function_name: str
    existing_layers: List[str]


def load_layer_arn(layer_arg: Optional[str], app_dir: Path) -> str:
    if layer_arg:
        return layer_arg
    layer_file = app_dir / ".chalice" / "layer-arn.txt"
    if not layer_file.exists():
        raise LayerAttachError(
            "Layer ARN not provided and .chalice/layer-arn.txt not found"
        )
    arn = layer_file.read_text(encoding="utf-8").strip()
    if not arn:
        raise LayerAttachError("Layer ARN file is empty")
    return arn


def list_chalice_functions(stage: str, lambda_client) -> List[str]:
    prefix = f"shopping-assistant-api-{stage}"
    functions: List[str] = []
    paginator = lambda_client.get_paginator("list_functions")

    for page in paginator.paginate():
        for function in page.get("Functions", []):
            name = function.get("FunctionName", "")
            if name.startswith(prefix):
                functions.append(name)

    return functions


def fetch_function_layers(lambda_client, function_name: str) -> FunctionLayers:
    response = lambda_client.get_function_configuration(FunctionName=function_name)
    layers = [layer.get("Arn", "") for layer in response.get("Layers", [])]
    layers = [layer for layer in layers if layer]
    return FunctionLayers(function_name=function_name, existing_layers=layers)


def replace_layer_versions(
    current_layers: Iterable[str], new_layer_arn: str
) -> List[str]:
    new_layer_name = (
        new_layer_arn.split(":")[-2] if ":" in new_layer_arn else new_layer_arn
    )
    kept_layers = []
    for layer in current_layers:
        layer_name = layer.split(":")[-2] if ":" in layer else layer
        if layer_name != new_layer_name:
            kept_layers.append(layer)
    kept_layers.append(new_layer_arn)
    return kept_layers


def attach_layer_to_functions(*, stage: str, region: str, layer_arn: str) -> None:
    lambda_client = boto3.client("lambda", region_name=region)

    function_names = list_chalice_functions(stage, lambda_client)
    if not function_names:
        raise LayerAttachError(
            f"No Lambda functions found for stage '{stage}' (prefix shopping-assistant-api-{stage})"
        )

    log("INFO", f"Found {len(function_names)} function(s) to update")

    for function_name in function_names:
        details = fetch_function_layers(lambda_client, function_name)
        layers = replace_layer_versions(details.existing_layers, layer_arn)

        log("INFO", f"Updating {function_name} with {len(layers)} layer(s)")
        try:
            lambda_client.update_function_configuration(
                FunctionName=function_name,
                Layers=layers,
            )
        except ClientError as exc:
            raise LayerAttachError(
                f"Failed to update {function_name}: {exc.response.get('Error', {}).get('Message', exc)}"
            ) from exc

    log("INFO", "Layer attachment completed")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach a Lambda layer to all Chalice-managed functions"
    )
    parser.add_argument("layer_arn", nargs="?", help="Layer ARN to attach")
    parser.add_argument("--stage", default=DEFAULT_STAGE, help="Chalice stage name")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    scripts_dir = Path(__file__).resolve().parent
    app_dir = scripts_dir.parent

    try:
        layer_arn = load_layer_arn(args.layer_arn, app_dir)
        log("INFO", f"Layer ARN: {layer_arn}")
        log("INFO", f"Stage: {args.stage} | Region: {args.region}")
        attach_layer_to_functions(
            stage=args.stage, region=args.region, layer_arn=layer_arn
        )
    except LayerAttachError as exc:
        log("ERROR", str(exc))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
