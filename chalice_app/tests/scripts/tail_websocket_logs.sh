#!/bin/bash
# Tail WebSocket-related Lambda function logs simultaneously
# Usage: ./tail_websocket_logs.sh [follow|recent]

MODE=${1:-follow}
REGION="ap-southeast-1"

# WebSocket handler Lambda functions
CONNECT_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_connect"
MESSAGE_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_message"
DISCONNECT_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_disconnect"

echo "Monitoring WebSocket Lambda logs..."
echo "Mode: $MODE"
echo "Region: $REGION"
echo ""

if [ "$MODE" == "follow" ]; then
    echo "Following logs in real-time (Ctrl+C to stop)..."
    echo ""
    
    # Tail all three logs simultaneously with prefixes
    aws logs tail "$CONNECT_LOG" --follow --region "$REGION" --format short | sed 's/^/[CONNECT] /' &
    aws logs tail "$MESSAGE_LOG" --follow --region "$REGION" --format short | sed 's/^/[MESSAGE] /' &
    aws logs tail "$DISCONNECT_LOG" --follow --region "$REGION" --format short | sed 's/^/[DISCONNECT] /' &
    
    # Wait for all background processes
    wait
else
    echo "Showing recent logs (last 5 minutes)..."
    echo ""
    
    echo "=== CONNECT HANDLER ==="
    aws logs tail "$CONNECT_LOG" --since 5m --region "$REGION" --format short
    echo ""
    
    echo "=== MESSAGE HANDLER ==="
    aws logs tail "$MESSAGE_LOG" --since 5m --region "$REGION" --format short
    echo ""
    
    echo "=== DISCONNECT HANDLER ==="
    aws logs tail "$DISCONNECT_LOG" --since 5m --region "$REGION" --format short
fi

