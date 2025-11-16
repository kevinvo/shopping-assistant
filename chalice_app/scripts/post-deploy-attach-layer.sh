#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

STAGE=${1:-chalice-test}
if [ $# -gt 0 ]; then
    shift
fi

REGION=${1:-ap-southeast-1}
if [ $# -gt 0 ]; then
    shift
fi

# If next arg looks like a bare layer name (not starting with --), map it to --layer-name
LAYER_ARGS=()
if [[ $# -gt 0 ]]; then
  if [[ "$1" != --* ]]; then
    LAYER_ARGS=(--layer-name "$1")
    shift
  fi
fi

exec python "${SCRIPT_DIR}/post_deploy_attach_layer.py" --stage "$STAGE" --region "$REGION" "${LAYER_ARGS[@]}" "$@"

