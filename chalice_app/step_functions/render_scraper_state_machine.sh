#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT_TEMPLATE="${SCRIPT_DIR}/scraper_state_machine.json"
OUTPUT_FILE="${SCRIPT_DIR}/rendered_scraper_state_machine.json"

usage() {
  cat >&2 <<'EOF'
Usage: render_scraper_state_machine.sh <scraper-lambda-arn>
   or: render_scraper_state_machine.sh --stage <chalice-stage>

Options:
  --stage <chalice-stage>  Resolve the scraper Lambda ARN from .chalice/config.json
                           using the specified Chalice stage (e.g. chalice-test, prod).

Examples:
  ./render_scraper_state_machine.sh arn:aws:lambda:ap-southeast-1:123:function:shopping-assistant-api-chalice-test-scraper
  ./render_scraper_state_machine.sh --stage chalice-test
EOF
}

if [[ $# -eq 2 && "$1" == "--stage" ]]; then
  STAGE="$2"
  CONFIG_FILE="$(cd "${SCRIPT_DIR}/.." && pwd)/.chalice/config.json"

  if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "Config not found at ${CONFIG_FILE}" >&2
    exit 1
  fi

  LAMBDA_ARN=$(python3 - "${CONFIG_FILE}" "${STAGE}" <<'PY'
import json
import os
import sys

config_path, stage = sys.argv[1:3]

try:
    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
except FileNotFoundError:
    sys.stderr.write(f"Config not found at {config_path}\n")
    sys.exit(1)

try:
    stage_config = config["stages"][stage]
except KeyError:
    sys.stderr.write(f"Stage '{stage}' not found in {config_path}\n")
    sys.exit(1)

env_vars = stage_config.get("environment_variables", {})
lambda_arn_override = env_vars.get("SCRAPER_WORKER_LAMBDA_ARN")
if lambda_arn_override:
    sys.stdout.write(lambda_arn_override)
    sys.exit(0)

try:
    sm_arn = stage_config["environment_variables"]["SCRAPER_STATE_MACHINE_ARN"]
except KeyError:
    sys.stderr.write(
        f"Stage '{stage}' missing SCRAPER_STATE_MACHINE_ARN in environment_variables\n"
    )
    sys.exit(1)

arn_parts = sm_arn.split(":")
if len(arn_parts) < 6:
    sys.stderr.write(
        f"Invalid Step Functions ARN '{sm_arn}' for stage '{stage}'\n"
    )
    sys.exit(1)

region = arn_parts[3]
account_id = arn_parts[4]
resource_name = arn_parts[-1].split("/")[-1]
if not resource_name:
    sys.stderr.write(
        f"Could not determine resource name from Step Functions ARN '{sm_arn}'\n"
    )
    sys.exit(1)

lambda_arn = f"arn:aws:lambda:{region}:{account_id}:function:{resource_name}"
sys.stdout.write(lambda_arn)
PY
  )

  if [[ -z "${LAMBDA_ARN}" ]]; then
    echo "Failed to resolve Lambda ARN for stage '${STAGE}'" >&2
    exit 1
  fi

  OUTPUT_FILE="${SCRIPT_DIR}/rendered_scraper_state_machine.${STAGE}.json"
elif [[ $# -eq 1 ]]; then
  LAMBDA_ARN="$1"
else
  usage
  exit 1
fi

if [[ ! -f "${INPUT_TEMPLATE}" ]]; then
  echo "Template not found at ${INPUT_TEMPLATE}" >&2
  exit 1
fi

sed "s|\${SCRAPER_LAMBDA_ARN}|${LAMBDA_ARN}|g" "${INPUT_TEMPLATE}" > "${OUTPUT_FILE}"

echo "Rendered state machine written to ${OUTPUT_FILE}" \
  "using Lambda ARN ${LAMBDA_ARN}"

