#!/bin/bash
set -e

FUNCTION_NAME="${1:-scraper_worker}"
STAGE="${2:-chalice-test}"
REGION="${3:-ap-southeast-1}"

FULL_FUNCTION_NAME="shopping-assistant-api-${STAGE}-${FUNCTION_NAME}"

echo "Measuring cold start latency for: ${FULL_FUNCTION_NAME}"
echo "Region: ${REGION}"
echo ""

if ! aws lambda get-function --function-name "${FULL_FUNCTION_NAME}" --region "${REGION}" > /dev/null 2>&1; then
    echo "Error: Function ${FULL_FUNCTION_NAME} not found in region ${REGION}"
    exit 1
fi

echo "Step 1: Waiting for container to expire (15 minutes)..."
read -p "        Wait now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Waiting 15 minutes..."
    sleep 900
else
    echo "Skipping wait."
fi

echo ""
echo "Step 2: Invoking Lambda function..."
INVOCATION_RESULT=$(aws lambda invoke \
    --function-name "${FULL_FUNCTION_NAME}" \
    --region "${REGION}" \
    --payload '{}' \
    /tmp/lambda-response.json 2>&1)

if [ $? -ne 0 ]; then
    echo "Error invoking function: ${INVOCATION_RESULT}"
    exit 1
fi

echo "Invocation successful."
echo ""
echo "Step 3: Waiting for logs..."
sleep 5

echo ""
echo "Step 4: Analyzing cold start metrics..."
echo ""

python3 "$(dirname "$0")/analyze_cold_starts.py" \
    --function-name "${FULL_FUNCTION_NAME}" \
    --stage "${STAGE}" \
    --region "${REGION}" \
    --hours 1 \
    --output table

echo ""
echo "Done! Check CloudWatch Logs:"
echo "  https://console.aws.amazon.com/cloudwatch/home?region=${REGION}#logsV2:log-groups/log-group/%2Faws%2Flambda%2F${FULL_FUNCTION_NAME}"

