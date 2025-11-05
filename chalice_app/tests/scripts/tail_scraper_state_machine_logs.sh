#!/usr/bin/env bash

set -euo pipefail

REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-"ap-southeast-1"}}
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGES_FILE="${WORKSPACE_ROOT}/infrastructure/config/scraper_step_functions.json"
FOLLOW="--follow"
SINCE="5m"

usage() {
  cat <<EOF
Usage: $(basename "$0") [stage] [--stage <stage>] [--recent]

Tail CloudWatch Logs for a scraper Step Functions state machine.

Arguments:
  stage        Optional positional stage (defaults to chalice-test)

Options:
  --stage <stage>  Chalice stage key (overrides positional stage)
  --recent         Show the last ${SINCE} of logs instead of following in real time.

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
    --recent)
      FOLLOW=""
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

STATE_MACHINE_NAME=$(python3 - "$STAGES_FILE" "$STAGE" <<'PY'
import json
import sys

config_path, stage_name = sys.argv[1:3]

with open(config_path, "r", encoding="utf-8") as fp:
    data = json.load(fp)

stage_cfg = data.get(stage_name)
if not stage_cfg:
    sys.stderr.write(f"Unknown stage '{stage_name}' in {config_path}\n")
    sys.exit(1)

sys.stdout.write(stage_cfg["state_machine_name"])
PY
)

LOG_GROUP="/aws/vendedlogs/states/${STATE_MACHINE_NAME}"

echo "Tailing ${LOG_GROUP} in ${REGION} (${STAGE})"

if [[ -n "${FOLLOW}" ]]; then
  exec aws logs tail "${LOG_GROUP}" --region "${REGION}" --since "${SINCE}" --format short ${FOLLOW}
else
  exec aws logs tail "${LOG_GROUP}" --region "${REGION}" --since "${SINCE}" --format short
fi

