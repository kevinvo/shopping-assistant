#!/bin/bash
# Monitor all relevant Lambda logs for Phase 3 diagnosis
# Usage: ./monitor_all_logs.sh [follow|recent] [time_window]
#   follow: Follow logs in real-time
#   recent: Show recent logs (default: 5m, can specify: 1h, 30m, etc.)

MODE=${1:-follow}
TIME_WINDOW=${2:-5m}
REGION="ap-southeast-1"

# WebSocket handler Lambda functions
CONNECT_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_connect"
MESSAGE_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_message"
DISCONNECT_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_disconnect"

# Chat processor Lambda
CHAT_PROCESSOR_LOG="/aws/lambda/shopping-assistant-api-chalice-test-chat_processor"

# Function to check if log group exists and has logs
check_log_group() {
    local log_group=$1
    local name=$2
    
    # Check if log group exists
    if ! aws logs describe-log-groups --log-group-name-prefix "$log_group" --region "$REGION" --query "logGroups[?logGroupName=='$log_group'].logGroupName" --output text | grep -q "^$log_group$"; then
        echo "⚠️  Warning: Log group '$log_group' not found"
        return 1
    fi
    
    # Check if log stream exists (indicates there are logs)
    local streams=$(aws logs describe-log-streams --log-group-name "$log_group" --region "$REGION" --order-by LastEventTime --descending --max-items 1 --query "logStreams[0].logStreamName" --output text 2>/dev/null)
    
    if [ "$streams" == "None" ] || [ -z "$streams" ]; then
        echo "  (No logs found in this group yet)"
        return 1
    fi
    
    return 0
}

# Function to tail logs with error handling
tail_logs() {
    local log_group=$1
    local prefix=$2
    
    local output=$(aws logs tail "$log_group" --since "$TIME_WINDOW" --region "$REGION" --format short 2>&1)
    
    # Check for errors
    if echo "$output" | grep -q "ResourceNotFoundException\|AccessDeniedException"; then
        echo "  ❌ Error accessing log group (check permissions or group name)"
        return 1
    fi
    
    # Check if output is empty or only whitespace
    if [ -z "$output" ] || ! echo "$output" | grep -q "."; then
        echo "  (No logs in the last $TIME_WINDOW)"
    else
        echo "$output"
    fi
}

echo "======================================================================"
echo "Phase 3: Full Flow Log Monitoring"
echo "======================================================================"
echo "Mode: $MODE"
echo "Region: $REGION"
if [ "$MODE" == "recent" ]; then
    echo "Time window: $TIME_WINDOW"
fi
echo ""
echo "Monitoring:"
echo "  - WebSocket Connect: $CONNECT_LOG"
echo "  - WebSocket Message: $MESSAGE_LOG"
echo "  - WebSocket Disconnect: $DISCONNECT_LOG"
echo "  - Chat Processor: $CHAT_PROCESSOR_LOG"
echo ""
echo "======================================================================"
echo ""

if [ "$MODE" == "follow" ]; then
    echo "Following logs in real-time (Ctrl+C to stop)..."
    echo "Note: You may not see output until new logs arrive"
    echo ""
    
    # Tail all logs simultaneously with prefixes
    aws logs tail "$CONNECT_LOG" --follow --region "$REGION" --format short 2>&1 | sed 's/^/[WS-CONNECT] /' &
    aws logs tail "$MESSAGE_LOG" --follow --region "$REGION" --format short 2>&1 | sed 's/^/[WS-MESSAGE] /' &
    aws logs tail "$DISCONNECT_LOG" --follow --region "$REGION" --format short 2>&1 | sed 's/^/[WS-DISCONNECT] /' &
    aws logs tail "$CHAT_PROCESSOR_LOG" --follow --region "$REGION" --format short 2>&1 | sed 's/^/[CHAT-PROC] /' &
    
    # Wait for all background processes
    wait
else
    echo "Showing recent logs (last $TIME_WINDOW)..."
    echo ""
    
    echo "=== WEBSOCKET CONNECT HANDLER ==="
    tail_logs "$CONNECT_LOG" "[WS-CONNECT]"
    echo ""
    
    echo "=== WEBSOCKET MESSAGE HANDLER ==="
    tail_logs "$MESSAGE_LOG" "[WS-MESSAGE]"
    echo ""
    
    echo "=== WEBSOCKET DISCONNECT HANDLER ==="
    tail_logs "$DISCONNECT_LOG" "[WS-DISCONNECT]"
    echo ""
    
    echo "=== CHAT PROCESSOR HANDLER ==="
    tail_logs "$CHAT_PROCESSOR_LOG" "[CHAT-PROC]"
    echo ""
    
    echo "💡 Tip: Use './monitor_all_logs.sh recent 1h' to see logs from the last hour"
fi

