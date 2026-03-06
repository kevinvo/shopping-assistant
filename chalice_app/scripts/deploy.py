#!/usr/bin/env python3
"""Unified Chalice deployment script with layer attachment and validation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import boto3
from botocore.exceptions import ClientError


DEFAULT_REGION = "ap-southeast-1"
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_VISIBLE_MESSAGES = 10
DEFAULT_MAX_INFLIGHT_MESSAGES = 20
DEFAULT_LAYER_NAME = "shopping-assistant-chalice-layer"
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 5

# WebSocket handler suffixes whose Lambda resource-based policies are pruned
# before each deploy to prevent PolicyLengthExceededException accumulation.
WEBSOCKET_HANDLER_SUFFIXES = [
    "websocket_connect",
    "websocket_message",
    "websocket_disconnect",
]

# Maps Chalice stage name to the CDK infrastructure env name used for SSM paths.
STAGE_TO_ENV: Dict[str, str] = {
    "chalice-test": "test",
    "chalice-prod": "prod",
}

# SSM parameter keys → Chalice env var names that get injected before deploy.
SSM_ENV_VAR_MAPPING: Dict[str, str] = {
    "chat-queue-url": "CHAT_PROCESSING_QUEUE_URL",
    "eval-queue-url": "EVALUATION_QUEUE_URL",
    "sns-alert-arn": "ERROR_ALERT_TOPIC_ARN",
    "athena-output-bucket": "ATHENA_OUTPUT_BUCKET",
}


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


class DeployError(RuntimeError):
    """Raised when deployment or validation fails."""


class LayerAttachError(RuntimeError):
    """Raised when layer attachment fails."""


@dataclass
class EventMapping:
    stage: str
    function_name: str
    function_arn: str
    uuid: str
    queue_url: str
    queue_arn: str
    batch_size: int


@dataclass
class FunctionLayers:
    function_name: str
    existing_layers: List[str]


def load_config(stage: str, config_path: Path) -> Dict:
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    stages = config.get("stages", {})
    if stage not in stages:
        raise DeployError(f"Stage '{stage}' not found in {config_path}")
    return config


def inject_layer_into_config(config_path: Path, stage: str, layer_arn: str) -> None:
    """Inject the layer ARN into Chalice config so it deploys atomically with code."""
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    config["stages"][stage]["layers"] = [layer_arn]

    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    log("INFO", f"Injected layer into config.json for stage '{stage}': {layer_arn}")


def inject_env_vars_from_ssm(stage: str, region: str, config_path: Path) -> None:
    """Read dynamic resource values from SSM and patch config.json before deploy.

    Only applies to stages that have a matching env in STAGE_TO_ENV. For stages
    that are not listed (e.g. 'dev'), this is a no-op.
    """
    env = STAGE_TO_ENV.get(stage)
    if env is None:
        log("INFO", f"No SSM env mapping for stage '{stage}', skipping SSM injection")
        return

    ssm_client = boto3.client("ssm", region_name=region)
    injected: Dict[str, str] = {}

    for ssm_key, env_var_name in SSM_ENV_VAR_MAPPING.items():
        param_name = f"/shopping-assistant/{env}/{ssm_key}"
        try:
            response = ssm_client.get_parameter(Name=param_name)
            value = response["Parameter"]["Value"]
            injected[env_var_name] = value
        except ssm_client.exceptions.ParameterNotFound:
            log("WARN", f"SSM parameter not found: {param_name} (skipping)")
        except Exception as exc:
            log("WARN", f"Failed to read SSM parameter {param_name}: {exc} (skipping)")

    if not injected:
        log("INFO", "No SSM values injected (all parameters missing or skipped)")
        return

    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    env_vars = (
        config.setdefault("stages", {})
        .setdefault(stage, {})
        .setdefault("environment_variables", {})
    )
    env_vars.update(injected)

    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    log(
        "INFO",
        f"Injected {len(injected)} SSM value(s) into config.json for stage '{stage}': {list(injected.keys())}",
    )

    # If we got an Athena bucket, also patch the IAM policy file to replace the placeholder ARN.
    athena_bucket = injected.get("ATHENA_OUTPUT_BUCKET")
    if athena_bucket:
        iam_policy_file = config.get("stages", {}).get(stage, {}).get("iam_policy_file")
        policy_file = (
            config_path.parent / iam_policy_file
            if iam_policy_file
            else config_path.parent / f"policy-{stage}.json"
        )
        if policy_file.exists():
            policy_text = policy_file.read_text(encoding="utf-8")
            if "INJECTED_BY_DEPLOY_ATHENA_BUCKET" in policy_text:
                bucket_arn = f"arn:aws:s3:::{athena_bucket}"
                policy_text = policy_text.replace(
                    '"INJECTED_BY_DEPLOY_ATHENA_BUCKET"',
                    f'"{bucket_arn}"',
                )
                policy_text = policy_text.replace(
                    '"INJECTED_BY_DEPLOY_ATHENA_BUCKET/*"',
                    f'"{bucket_arn}/*"',
                )
                policy_file.write_text(policy_text, encoding="utf-8")
                log(
                    "INFO",
                    f"Patched Athena bucket ARN in {policy_file.name}: {bucket_arn}",
                )


def update_websocket_domain(stage: str, region: str, config_path: Path) -> None:
    """Resolve the deployed WebSocket API endpoint and patch WEBSOCKET_DOMAIN in config.json.

    This is a best-effort step: if the API cannot be found, a warning is logged
    and the placeholder value is left in place.
    """
    apigw_client = boto3.client("apigatewayv2", region_name=region)
    # Chalice names the WebSocket API after the app name + stage, e.g.:
    # "shopping-assistant-api-chalice-prod" or similar. We search by matching
    # the stage prefix in the API name.
    search_name = f"shopping-assistant-api-{stage}"
    try:
        apis = apigw_client.get_apis().get("Items", [])
        ws_apis = [
            a
            for a in apis
            if a.get("ProtocolType") == "WEBSOCKET" and search_name in a.get("Name", "")
        ]
    except Exception as exc:
        log(
            "WARN",
            f"Could not list API Gateway v2 APIs: {exc} — WEBSOCKET_DOMAIN not updated",
        )
        return

    if not ws_apis:
        log(
            "WARN",
            f"No WebSocket API found matching '{search_name}' — WEBSOCKET_DOMAIN not updated",
        )
        return

    api = ws_apis[0]
    api_id = api["ApiId"]
    domain = f"{api_id}.execute-api.{region}.amazonaws.com"

    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    env_vars = config.get("stages", {}).get(stage, {}).get("environment_variables", {})
    env_vars["WEBSOCKET_DOMAIN"] = domain

    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    log("INFO", f"Updated WEBSOCKET_DOMAIN to '{domain}' for stage '{stage}'")


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


def prune_websocket_permissions(stage: str, region: str) -> None:
    """Remove stale API Gateway v2 permission statements from WebSocket Lambda functions.

    Each deploy creates a new WebSocket API (because the Chalice deployed-state
    file is not persisted in CI), causing Chalice to call add_permission for the
    new API ID without removing statements from previous API IDs.  Over many
    deploys the resource-based policy grows past the 20 480-byte AWS limit.

    Pruning all apigateway.amazonaws.com statements before deploy gives Chalice a
    clean slate; it will re-add exactly the statements needed for the current API.
    """
    lambda_client = boto3.client("lambda", region_name=region)

    for suffix in WEBSOCKET_HANDLER_SUFFIXES:
        function_name = f"shopping-assistant-api-{stage}-{suffix}"
        try:
            policy_resp = lambda_client.get_policy(FunctionName=function_name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                # Function or policy not yet created; nothing to prune.
                continue
            log("WARN", f"Could not fetch policy for {function_name}: {exc}")
            continue

        policy = json.loads(policy_resp["Policy"])
        stale_sids = [
            stmt["Sid"]
            for stmt in policy.get("Statement", [])
            if stmt.get("Principal", {}).get("Service") == "apigateway.amazonaws.com"
        ]

        if not stale_sids:
            log("INFO", f"No stale apigateway permissions on {function_name}")
            continue

        for sid in stale_sids:
            try:
                lambda_client.remove_permission(
                    FunctionName=function_name, StatementId=sid
                )
                log("INFO", f"Pruned permission '{sid}' from {function_name}")
            except ClientError as exc:
                log(
                    "WARN",
                    f"Failed to remove permission '{sid}' from {function_name}: {exc}",
                )


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


def fetch_latest_layer_arn(layer_name: str, region: str) -> str:
    client = boto3.client("lambda", region_name=region)
    try:
        response = client.list_layer_versions(
            LayerName=layer_name,
            MaxItems=1,
        )
    except ClientError as exc:
        raise LayerAttachError(
            f"Failed to list layer versions for {layer_name}: {exc.response.get('Error', {}).get('Message', exc)}"
        ) from exc

    versions = response.get("LayerVersions", [])
    if not versions:
        raise LayerAttachError(
            f"No versions found for layer '{layer_name}' in region '{region}'"
        )

    return versions[0]["LayerVersionArn"]


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


def verify_layer_attachment(stage: str, region: str, layer_arn: str) -> bool:
    lambda_client = boto3.client("lambda", region_name=region)
    function_names = list_chalice_functions(stage, lambda_client)
    if not function_names:
        raise DeployError(
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


def attach_and_verify_layer(
    stage: str,
    region: str,
    layer_name: str,
    max_retries: int,
    retry_delay: int,
) -> None:
    """Attach layer to all functions with retries and verification."""
    try:
        layer_arn = fetch_latest_layer_arn(layer_name, region)
    except LayerAttachError as exc:
        raise DeployError(str(exc)) from exc

    log(
        "INFO", "======================================================================"
    )
    log("INFO", "Post-Deployment Layer Attachment")
    log(
        "INFO", "======================================================================"
    )
    log("INFO", f"Stage: {stage}")
    log("INFO", f"Region: {region}")
    log("INFO", f"Layer Name: {layer_name}")
    log("INFO", f"Layer ARN: {layer_arn}")

    attempt = 0
    while attempt < max_retries:
        attempt += 1
        log("INFO", f"Attempt {attempt} of {max_retries}")
        try:
            attach_layer_to_functions(stage=stage, region=region, layer_arn=layer_arn)
        except LayerAttachError as exc:
            log("WARN", f"Layer attachment failed: {exc}")
        else:
            if verify_layer_attachment(stage, region, layer_arn):
                log("INFO", "✅ Post-deployment layer attachment complete!")
                return
            log("WARN", "Layer verification failed; retrying")

        if attempt < max_retries:
            log("INFO", f"Waiting {retry_delay} seconds before retry...")
            time.sleep(retry_delay)

    raise DeployError("Failed to attach layer after retries")


def ensure_post_deploy(
    stage: str,
    env_vars: Dict[str, str],
    region: str,
    max_visible: int,
    max_inflight: int,
    max_retries: int = 3,
    retry_delay: int = 10,
) -> None:
    lambda_client = boto3.client("lambda", region_name=region)
    sqs_client = boto3.client("sqs", region_name=region)

    # Ensure chat processor mapping exists and enabled.
    function_name = f"shopping-assistant-api-{stage}-chat_processor"
    mappings = lambda_client.list_event_source_mappings(FunctionName=function_name)
    if not mappings.get("EventSourceMappings"):
        raise DeployError(f"No event-source mapping found for {function_name}")

    mapping_entry = mappings["EventSourceMappings"][0]
    state = mapping_entry.get("State")
    if state == "Disabled":
        log(
            "WARN",
            f"Event-source mapping for {function_name} is Disabled; re-enabling...",
        )
        mapping_uuid = mapping_entry["UUID"]
        lambda_client.update_event_source_mapping(UUID=mapping_uuid, Enabled=True)
        # Wait for the mapping to reach Enabled state
        for _ in range(12):
            time.sleep(5)
            check = lambda_client.list_event_source_mappings(FunctionName=function_name)
            state = check["EventSourceMappings"][0].get("State", "")
            log("INFO", f"Event-source mapping state: {state}")
            if state == "Enabled":
                break
        else:
            raise DeployError(
                f"Event-source mapping for {function_name} did not reach Enabled state (current: {state})"
            )
    elif state != "Enabled":
        raise DeployError(f"Event-source mapping for {function_name} is '{state}'")

    queue_url = env_vars.get("CHAT_PROCESSING_QUEUE_URL")
    if not queue_url:
        raise DeployError("CHAT_PROCESSING_QUEUE_URL not configured")

    # Retry checking SQS backlog to allow in-flight messages to complete
    for attempt in range(max_retries):
        attrs = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )
        visible = int(attrs["Attributes"].get("ApproximateNumberOfMessages", 0))
        inflight = int(
            attrs["Attributes"].get("ApproximateNumberOfMessagesNotVisible", 0)
        )

        log(
            "INFO",
            f"SQS backlog (attempt {attempt + 1}/{max_retries}): visible={visible}, inflight={inflight}",
        )

        # Check thresholds
        if visible > max_visible:
            if attempt < max_retries - 1:
                log(
                    "WARN",
                    f"Visible messages ({visible}) exceed threshold ({max_visible}), waiting {retry_delay}s...",
                )
                time.sleep(retry_delay)
                continue
            raise DeployError(
                f"Visible SQS messages ({visible}) exceed threshold ({max_visible}) after {max_retries} attempts"
            )

        if inflight > max_inflight:
            if attempt < max_retries - 1:
                log(
                    "WARN",
                    f"In-flight messages ({inflight}) exceed threshold ({max_inflight}), waiting {retry_delay}s for processing to complete...",
                )
                time.sleep(retry_delay)
                continue
            raise DeployError(
                f"In-flight SQS messages ({inflight}) exceed threshold ({max_inflight}) after {max_retries} attempts"
            )

        # Both checks passed
        log(
            "INFO",
            f"✅ SQS backlog within thresholds: visible={visible}, inflight={inflight}",
        )
        return

    # Should not reach here, but just in case
    raise DeployError("SQS backlog validation failed after all retries")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Chalice deployment with layer attachment"
    )
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
    parser.add_argument(
        "--layer-name",
        default=DEFAULT_LAYER_NAME,
        help="Name of the Lambda layer to attach",
    )
    parser.add_argument(
        "--layer-max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Number of layer attachment retries",
    )
    parser.add_argument(
        "--layer-retry-delay",
        type=int,
        default=DEFAULT_RETRY_DELAY,
        help="Seconds to wait between layer attachment retries",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    scripts_dir = Path(__file__).resolve().parent
    app_dir = scripts_dir.parent
    config_path = app_dir / ".chalice" / "config.json"

    config = load_config(args.stage, config_path)

    # Inject dynamic values (queue URLs, SNS ARN, Athena bucket) from SSM before deploy.
    inject_env_vars_from_ssm(args.stage, args.region, config_path)

    # Reload config after SSM injection so env_vars reflects injected values.
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

    # Inject layer ARN into config.json BEFORE deploy so Chalice deploys
    # code + layer atomically, eliminating the window where new code runs
    # without the layer (which caused ModuleNotFoundError during deploys).
    try:
        layer_arn = fetch_latest_layer_arn(args.layer_name, args.region)
        inject_layer_into_config(config_path, args.stage, layer_arn)
    except (LayerAttachError, DeployError) as exc:
        log("ERROR", f"Failed to inject layer into config: {exc}")
        return 1

    # Prune stale API Gateway v2 permission statements from WebSocket Lambda
    # functions before deploying.  Without this, each deploy that creates a new
    # WebSocket API appends a new add_permission statement without removing the
    # old ones, eventually exceeding the 20 480-byte resource-policy limit.
    prune_websocket_permissions(args.stage, args.region)

    try:
        retry_chalice_deploy(args.stage, args.max_attempts, app_dir)
    except DeployError as exc:
        log("ERROR", str(exc))
        return 1

    # Resolve and persist the WebSocket domain after Chalice creates/updates the API.
    update_websocket_domain(args.stage, args.region, config_path)

    # Verify layer is attached (should already be via config.json, this is a safety check).
    try:
        if not verify_layer_attachment(args.stage, args.region, layer_arn):
            log("WARN", "Layer not detected after deploy, re-attaching...")
            attach_and_verify_layer(
                args.stage,
                args.region,
                args.layer_name,
                args.layer_max_retries,
                args.layer_retry_delay,
            )
    except DeployError as exc:
        log("ERROR", f"Layer verification/attachment failed: {exc}")
        return 1

    # Update function runtimes to Python 3.12 (Chalice doesn't respect runtime in config.json)
    try:
        log("INFO", "Updating function runtimes to Python 3.12...")
        update_runtime_script = scripts_dir / "update_function_runtimes.py"
        result = subprocess.run(
            [
                sys.executable,
                str(update_runtime_script),
                "--stage",
                args.stage,
                "--region",
                args.region,
            ],
            cwd=app_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            log("WARN", f"Runtime update had issues: {result.stderr}")
        else:
            log("INFO", "✅ Function runtimes updated to Python 3.12")
    except Exception as exc:
        log("WARN", f"Runtime update failed (non-blocking): {exc}")

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
