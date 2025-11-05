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

exec python "${SCRIPT_DIR}/post_deploy_attach_layer.py" --stage "$STAGE" --region "$REGION" "$@"

