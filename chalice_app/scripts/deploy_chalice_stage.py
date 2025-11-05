#!/usr/bin/env python3
"""Reliable Chalice deploy wrapper with mapping reconciliation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


DEFAULT_REGION = "ap-southeast-1"
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_VISIBLE_MESSAGES = 10
DEFAULT_MAX_INFLIGHT_MESSAGES = 5


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


class DeployError(RuntimeError):
    """Raised when deployment or validation fails."""


@dataclass
class EventMapping:
    stage: str
    function_name: str
    function_arn: str
    uuid: str
    queue_url: str
    queue_arn: str
    batch_size: int


def load_config(stage: str, config_path: Path) -> Dict:
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    stages = config.get("stages", {})
    if stage not in stages:
        raise DeployError(f"Stage '{stage}' not found in {config_path}")
    return config


def stage_env(stage: str, config: Dict) -> Dict[str, str]:
    return config.get("stages", {}).get(stage, {}).get("environment_variables", {})


def list_event_mappings(
    stage: str, env_vars: Dict[str, str], region: str
) -> List[EventMapping]:
    client = boto3.client("lambda", region_name=region)
    entries: List[EventMapping] = []

    sqs_functions = {
        "chat_processor": ("CHAT_PROCESSING_QUEUE_URL", 1),
        "evaluator": ("EVALUATION_QUEUE_URL", 10),
    }

    sqs_client = boto3.client("sqs", region_name=region)

    for handler, (env_key, batch_size) in sqs_functions.items():
        queue_url = env_vars.get(env_key)
        if not queue_url:
            continue

        try:
            queue_attrs = sqs_client.get_queue_attributes(
                QueueUrl=queue_url, AttributeNames=["QueueArn"]
            )
        except ClientError as exc:
            raise DeployError(
                f"Failed to get attributes for queue {queue_url}: {exc}"
            ) from exc

        queue_arn = queue_attrs["Attributes"].get("QueueArn")
        if not queue_arn:
            raise DeployError(f"QueueArn missing for {queue_url}")

        function_name = f"shopping-assistant-api-{stage}-{handler}"

        response = client.list_event_source_mappings(FunctionName=function_name)
        for mapping in response.get("EventSourceMappings", []):
            entries.append(
                EventMapping(
                    stage=stage,
                    function_name=function_name,
                    function_arn=mapping.get("FunctionArn", ""),
                    uuid=mapping["UUID"],
                    queue_url=queue_url,
                    queue_arn=queue_arn,
                    batch_size=batch_size,
                )
            )

    return entries


def load_deployed_state(stage: str, config_path: Path) -> Dict:
    deployed_dir = config_path.parent / "deployed"
    deployed_dir.mkdir(parents=True, exist_ok=True)
    deployed_file = deployed_dir / f"{stage}.json"

    if deployed_file.exists():
        with deployed_file.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    return {
        "schema_version": "2.0",
        "resources": [],
        "backend": "api",
    }


def update_deployed_config(
    stage: str, deployed_state: Dict, mappings: List[EventMapping]
) -> Dict:
    resource_list = deployed_state.setdefault("resources", [])
    prefix = f"shopping-assistant-api-{stage}-"

    def find_resource(name: str) -> Optional[Dict]:
        return next((res for res in resource_list if res.get("name") == name), None)

    for mapping in mappings:
        suffix = mapping.function_name
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix) :]
        resource_name = f"{suffix}-sqs-event-source"
        queue_name = mapping.queue_url.rsplit("/", 1)[-1]
        lambda_arn = mapping.function_arn
        if not lambda_arn:
            parts = mapping.queue_arn.split(":")
            if len(parts) >= 5:
                region_part = parts[3]
                account_part = parts[4]
                lambda_arn = f"arn:aws:lambda:{region_part}:{account_part}:function:{mapping.function_name}"

        payload = {
            "name": resource_name,
            "resource_type": "sqs_event",
            "queue_arn": mapping.queue_arn,
            "event_uuid": mapping.uuid,
            "queue": queue_name,
            "lambda_arn": lambda_arn,
        }

        existing = find_resource(resource_name)
        if existing is None:
            resource_list.append(payload)
        else:
            existing.update(payload)

    return deployed_state


def write_deployed_config(stage: str, deployed_state: Dict, config_path: Path) -> None:
    deployed_dir = config_path.parent / "deployed"
    deployed_dir.mkdir(parents=True, exist_ok=True)
    deployed_file = deployed_dir / f"{stage}.json"
    with deployed_file.open("w", encoding="utf-8") as fh:
        json.dump(deployed_state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def run_command(cmd: List[str], cwd: Optional[Path] = None) -> None:
    log("INFO", f"Executing: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def retry_chalice_deploy(stage: str, max_attempts: int, app_dir: Path) -> None:
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            log("INFO", f"Running chalice deploy (attempt {attempts}/{max_attempts})")
            run_command(["chalice", "deploy", "--stage", stage], cwd=app_dir)
            return
        except subprocess.CalledProcessError as exc:
            if attempts >= max_attempts:
                raise DeployError(
                    f"chalice deploy failed after {attempts} attempts"
                ) from exc
            log("WARN", f"chalice deploy failed (attempt {attempts}), retrying in 10s")
            time.sleep(10)


def ensure_post_deploy(
    stage: str,
    env_vars: Dict[str, str],
    region: str,
    max_visible: int,
    max_inflight: int,
) -> None:
    lambda_client = boto3.client("lambda", region_name=region)
    sqs_client = boto3.client("sqs", region_name=region)

    # Ensure chat processor mapping exists and enabled.
    function_name = f"shopping-assistant-api-{stage}-chat_processor"
    mappings = lambda_client.list_event_source_mappings(FunctionName=function_name)
    if not mappings.get("EventSourceMappings"):
        raise DeployError(f"No event-source mapping found for {function_name}")

    state = mappings["EventSourceMappings"][0].get("State")
    if state != "Enabled":
        raise DeployError(f"Event-source mapping for {function_name} is '{state}'")

    queue_url = env_vars.get("CHAT_PROCESSING_QUEUE_URL")
    if not queue_url:
        raise DeployError("CHAT_PROCESSING_QUEUE_URL not configured")

    attrs = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )
    visible = int(attrs["Attributes"].get("ApproximateNumberOfMessages", 0))
    inflight = int(attrs["Attributes"].get("ApproximateNumberOfMessagesNotVisible", 0))

    log("INFO", f"SQS backlog: visible={visible}, inflight={inflight}")

    if visible > max_visible:
        raise DeployError(
            f"Visible SQS messages ({visible}) exceed threshold ({max_visible})"
        )
    if inflight > max_inflight:
        raise DeployError(
            f"In-flight SQS messages ({inflight}) exceed threshold ({max_inflight})"
        )


def attach_layer(stage: str, region: str, scripts_dir: Path) -> None:
    script = scripts_dir / "post_deploy_attach_layer.py"
    run_command(
        [
            "python",
            str(script),
            "--stage",
            stage,
            "--region",
            region,
        ]
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reliable Chalice deploy wrapper")
    parser.add_argument(
        "--stage", default="chalice-test", help="Chalice stage to deploy"
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="Maximum chalice deploy attempts",
    )
    parser.add_argument(
        "--max-visible",
        type=int,
        default=DEFAULT_MAX_VISIBLE_MESSAGES,
        help="Maximum allowed visible SQS messages",
    )
    parser.add_argument(
        "--max-inflight",
        type=int,
        default=DEFAULT_MAX_INFLIGHT_MESSAGES,
        help="Maximum allowed in-flight SQS messages",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    scripts_dir = Path(__file__).resolve().parent
    app_dir = scripts_dir.parent
    config_path = app_dir / ".chalice" / "config.json"

    config = load_config(args.stage, config_path)
    env_vars = stage_env(args.stage, config)

    log("INFO", f"Deploying Chalice stage '{args.stage}' in region {args.region}")

    # Capture existing mappings and update deployed config to reuse them.
    mappings = list_event_mappings(args.stage, env_vars, args.region)
    if mappings:
        log(
            "INFO",
            f"Found {len(mappings)} existing SQS event source mapping(s) to reuse",
        )
        deployed_state = load_deployed_state(args.stage, config_path)
        deployed_config = update_deployed_config(args.stage, deployed_state, mappings)
        write_deployed_config(args.stage, deployed_config, config_path)
    else:
        log(
            "INFO",
            "No existing SQS event source mappings found; Chalice will create them",
        )

    try:
        retry_chalice_deploy(args.stage, args.max_attempts, app_dir)
    except DeployError as exc:
        log("ERROR", str(exc))
        return 1

    # Attach layer after successful deploy.
    try:
        attach_layer(args.stage, args.region, scripts_dir)
    except subprocess.CalledProcessError as exc:
        log("ERROR", f"Layer attachment failed: {exc}")
        return 1

    # Validate mapping and SQS backlog.
    try:
        ensure_post_deploy(
            args.stage, env_vars, args.region, args.max_visible, args.max_inflight
        )
    except DeployError as exc:
        log("ERROR", str(exc))
        return 1

    log("INFO", "Post-deploy validation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
