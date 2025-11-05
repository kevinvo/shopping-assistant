#!/usr/bin/env bash

set -euo pipefail

REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-"ap-southeast-1"}}
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGES_FILE="${WORKSPACE_ROOT}/infrastructure/config/scraper_step_functions.json"
DEFAULT_INPUT_SENTINEL="__DEFAULT__"

usage() {
  cat <<EOF
Usage: $(basename "$0") [stage] [--stage <stage>] [--input <json>] [--name <execution-name>]

Start an execution of the scraper Step Functions state machine.

Arguments:
  stage              Optional positional stage (defaults to chalice-test)

Options:
  --stage <stage>    Chalice stage key (overrides positional stage)
  --input <json>     JSON payload for the execution input (default: CloudWatch scheduled event)
  --name <name>      Execution name; defaults to scraper-<timestamp>

Environment:
  AWS_REGION / AWS_DEFAULT_REGION  AWS region (default: ${REGION})
  STAGES_FILE                      Override path to scraper_step_functions.json
EOF
}

STAGE="chalice-test"

if [[ $# -gt 0 && "$1" != --* ]]; then
  STAGE="$1"
  shift
fi

EXECUTION_INPUT="${DEFAULT_INPUT_SENTINEL}"
EXECUTION_NAME="scraper-$(date -u +%Y%m%dT%H%M%SZ)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)
      shift
      if [[ $# -eq 0 ]]; then
        echo "--stage requires a value" >&2
        usage >&2
        exit 1
      fi
      STAGE="$1"
      ;;
    --input)
      shift
      if [[ $# -eq 0 ]]; then
        echo "--input requires a value" >&2
        usage >&2
        exit 1
      fi
      EXECUTION_INPUT="$1"
      ;;
    --name)
      shift
      if [[ $# -eq 0 ]]; then
        echo "--name requires a value" >&2
        usage >&2
        exit 1
      fi
      EXECUTION_NAME="$1"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
 done

if [[ -z "${STAGE}" ]]; then
  echo "Stage must not be empty" >&2
  usage >&2
  exit 1
fi

if [[ ! -f "${STAGES_FILE}" ]]; then
  echo "Stage config file not found at ${STAGES_FILE}" >&2
  exit 1
fi

PY_OUTPUT=$(python3 - "$STAGES_FILE" "$STAGE" <<'PY'
import json
import sys
import uuid
from datetime import datetime, timezone

config_path, stage_name = sys.argv[1:3]

with open(config_path, "r", encoding="utf-8") as fp:
    data = json.load(fp)

stage_cfg = data.get(stage_name)
if not stage_cfg:
    sys.stderr.write(f"Unknown stage '{stage_name}' in {config_path}\n")
    sys.exit(1)

arn = stage_cfg.get("state_machine_arn")
if not arn:
    region = stage_cfg.get("region")
    account = stage_cfg.get("account")
    state_machine_name = stage_cfg["state_machine_name"]
    if not region or not account:
        sys.stderr.write(
            "Stage configuration must provide either 'state_machine_arn' or both 'region' and 'account'.\n"
        )
        sys.exit(1)
    arn = f"arn:aws:states:{region}:{account}:stateMachine:{state_machine_name}"
else:
    region = stage_cfg.get("region")
    account = stage_cfg.get("account")
    state_machine_name = stage_cfg.get("state_machine_name")

if not region:
    region = "ap-southeast-1"
if not account:
    account = "000000000000"

rule_arn = f"arn:aws:events:{region}:{account}:rule/manual-scraper-stepfunction-{stage_name}"
current_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

cloudwatch_event = {
    "version": "0",
    "id": str(uuid.uuid4()),
    "detail-type": "Scheduled Event",
    "source": "aws.events",
    "account": account,
    "time": current_time,
    "region": region,
    "resources": [rule_arn],
    "detail": {},
}

print(arn)
print(json.dumps(cloudwatch_event))
PY
)

STATE_MACHINE_ARN=$(printf "%s" "$PY_OUTPUT" | sed -n '1p')
DEFAULT_EVENT_JSON=$(printf "%s" "$PY_OUTPUT" | sed -n '2p')

if [[ "${EXECUTION_INPUT}" == "${DEFAULT_INPUT_SENTINEL}" ]]; then
  EXECUTION_INPUT="${DEFAULT_EVENT_JSON}"
fi

echo "Starting execution ${EXECUTION_NAME} on ${STATE_MACHINE_ARN}"
aws stepfunctions start-execution \
  --state-machine-arn "${STATE_MACHINE_ARN}" \
  --name "${EXECUTION_NAME}" \
  --input "${EXECUTION_INPUT}" \
  --region "${REGION}"

