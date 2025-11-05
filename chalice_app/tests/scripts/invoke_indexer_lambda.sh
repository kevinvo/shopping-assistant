#!/usr/bin/env bash

set -euo pipefail

REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-"ap-southeast-1"}}
DEFAULT_STAGE="chalice-test"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

usage() {
  cat <<EOF
Usage: \
  \\
    \
  $(basename "$0") [stage] [--stage <stage>] [--payload <json>] [--payload-file <path>]

Invoke the Chalice-managed indexer Lambda function with an optional input payload.

Arguments:
  stage                Optional positional stage (defaults to ${DEFAULT_STAGE})

Options:
  --stage <stage>      Chalice stage key (overrides positional stage)
  --payload <json>     Inline JSON payload
  --payload-file <path>Path to a JSON file to use as payload
  --help, -h           Show this help message

Environment:
  AWS_REGION / AWS_DEFAULT_REGION  AWS region (default: ${REGION})
EOF
}

STAGE_ARG=""
PAYLOAD_INLINE=""
PAYLOAD_PATH=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)
      STAGE_ARG="$2"
      shift 2
      ;;
    --payload)
      PAYLOAD_INLINE="$2"
      shift 2
      ;;
    --payload-file)
      PAYLOAD_PATH="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ -n "$STAGE_ARG" ]]; then
  STAGE="$STAGE_ARG"
elif [[ ${#POSITIONAL[@]} -gt 0 ]]; then
  STAGE="${POSITIONAL[0]}"
else
  STAGE="$DEFAULT_STAGE"
fi

FUNCTION_NAME="shopping-assistant-api-${STAGE}-indexer"

TMP_PAYLOAD_FILE=""
cleanup() {
  [[ -n "$TMP_PAYLOAD_FILE" && -f "$TMP_PAYLOAD_FILE" ]] && rm -f "$TMP_PAYLOAD_FILE"
}
trap cleanup EXIT

if [[ -n "$PAYLOAD_PATH" ]]; then
  if [[ ! -f "$PAYLOAD_PATH" ]]; then
    echo "Payload file not found: $PAYLOAD_PATH" >&2
    exit 1
  fi
  PAYLOAD_FILE="$PAYLOAD_PATH"
else
  TMP_PAYLOAD_FILE="$(mktemp -t indexer-payload.XXXXXX)"
  if [[ -n "$PAYLOAD_INLINE" ]]; then
    printf '%s' "$PAYLOAD_INLINE" >"$TMP_PAYLOAD_FILE"
  else
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    UUID=$(uuidgen)
    NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
    cat >"$TMP_PAYLOAD_FILE" <<EOF
{
  "version": "0",
  "id": "${UUID}",
  "detail-type": "Scheduled Event",
  "source": "aws.events",
  "account": "${ACCOUNT_ID}",
  "time": "${NOW}",
  "region": "${REGION}",
  "resources": ["arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/manual-indexer-${STAGE}"],
  "detail": {}
}
EOF
  fi
  PAYLOAD_FILE="$TMP_PAYLOAD_FILE"
fi

RESPONSE_FILE="$(mktemp -t indexer-response.XXXXXX)"
trap 'cleanup; rm -f "$RESPONSE_FILE"' EXIT

echo "Invoking ${FUNCTION_NAME} in ${REGION} (${STAGE})"
aws lambda invoke \
  --function-name "$FUNCTION_NAME" \
  --payload "fileb://${PAYLOAD_FILE}" \
  --region "$REGION" \
  "$RESPONSE_FILE"

echo "Invocation response:"
cat "$RESPONSE_FILE"

