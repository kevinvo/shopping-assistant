#!/bin/bash
# Monitor Lambda logs
# Usage:
#   ./monitor_all_logs.sh [follow|recent] [time_window] [targets...]
#     follow       Follow logs in real-time
#     recent       Show recent logs (default window 5m, override with time_window like 1h/30m/5m)
#     time_window  Optional for 'recent' (default: 5m). Ignored for 'follow'
#     targets      Optional list of which logs to include. Supported aliases:
#                    connect, message, disconnect, chat
#                  You can also pass full log group names to tail directly.
# Examples:
#   ./monitor_all_logs.sh follow
#   ./monitor_all_logs.sh recent 15m connect message
#   ./monitor_all_logs.sh follow /aws/lambda/shopping-assistant-api-chalice-test-websocket_connect

MODE=${1:-follow}
TIME_WINDOW=${2:-5m}
REGION="ap-southeast-1"

# WebSocket handler Lambda functions
CONNECT_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_connect"
MESSAGE_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_message"
DISCONNECT_LOG="/aws/lambda/shopping-assistant-api-chalice-test-websocket_disconnect"

# Chat processor Lambda
CHAT_PROCESSOR_LOG="/aws/lambda/shopping-assistant-api-chalice-test-chat_processor"
INDEXER_LOG="/aws/lambda/shopping-assistant-api-chalice-test-indexer"
SCRAPER_LOG="/aws/lambda/shopping-assistant-api-chalice-test-scraper"
SCRAPER_WORKER_LOG="/aws/lambda/shopping-assistant-api-chalice-test-scraper_worker"
GLUE_STARTER_LOG="/aws/lambda/shopping-assistant-api-chalice-test-glue_starter"
LAYER_CLEANUP_LOG="/aws/lambda/shopping-assistant-api-chalice-test-layer_cleanup"
KEEP_WARM_LOG="/aws/lambda/shopping-assistant-api-chalice-test-keep_websocket_warm"

# Build selected targets
shift_count=0
if [[ "$MODE" == "follow" ]]; then
  shift_count=1
else
  # MODE == recent; next param is TIME_WINDOW
  shift_count=2
fi

# Collect remaining args as target selectors (if any)
declare -a SELECTORS=()
if [[ $# -ge $shift_count ]]; then
  # shellcheck disable=SC2145
  for ((i=shift_count; i<=$#; i++)); do
    arg="${!i}"
    [[ -n "$arg" ]] && SELECTORS+=("$arg")
  done
fi

# If no selectors provided, prompt user interactively
if [[ ${#SELECTORS[@]} -eq 0 ]]; then
  echo ""
  echo "Select which functions to tail (comma-separated numbers):"
  echo "  1) WebSocket Connect"
  echo "  2) WebSocket Message"
  echo "  3) WebSocket Disconnect"
  echo "  4) Chat Processor"
  echo "  5) Indexer"
  echo "  6) Scraper (scheduler)"
  echo "  7) Scraper Worker"
  echo "  8) Glue Starter"
  echo "  9) Layer Cleanup"
  echo " 10) Keep WebSocket Warm"
  echo " 11) All of the above"
  echo ""
  read -r -p "Enter choice(s): " CHOICES
  if [[ -z "$CHOICES" ]]; then
    SELECTORS=("all")
  else
    # Normalize choices into selectors
    IFS=',' read -ra CH_ARR <<< "$CHOICES"
    for ch in "${CH_ARR[@]}"; do
      num="$(echo "$ch" | xargs)" # trim
      case "$num" in
        1) SELECTORS+=("connect") ;;
        2) SELECTORS+=("message") ;;
        3) SELECTORS+=("disconnect") ;;
        4) SELECTORS+=("chat") ;;
        5) SELECTORS+=("indexer") ;;
        6) SELECTORS+=("scraper") ;;
        7) SELECTORS+=("scraper_worker") ;;
        8) SELECTORS+=("glue_starter") ;;
        9) SELECTORS+=("layer_cleanup") ;;
        10) SELECTORS+=("keep_warm") ;;
        11) SELECTORS+=("all") ;;
        *) 
          # also allow entering full log groups directly
          if [[ "$num" == /aws/lambda/* ]]; then
            SELECTORS+=("$num")
          else
            echo "⚠️  Unknown choice '$num' - skipping"
          fi
          ;;
      esac
    done
  fi
fi

# Map a selector to a log group (supports aliases and full names)
map_target_to_log_group() {
  local sel="$1"
  case "$sel" in
    connect) echo "$CONNECT_LOG" ;;
    message) echo "$MESSAGE_LOG" ;;
    disconnect) echo "$DISCONNECT_LOG" ;;
    chat) echo "$CHAT_PROCESSOR_LOG" ;;
    indexer) echo "$INDEXER_LOG" ;;
    scraper) echo "$SCRAPER_LOG" ;;
    scraper_worker) echo "$SCRAPER_WORKER_LOG" ;;
    glue_starter) echo "$GLUE_STARTER_LOG" ;;
    layer_cleanup) echo "$LAYER_CLEANUP_LOG" ;;
    keep_warm) echo "$KEEP_WARM_LOG" ;;
    all)
      echo "$CONNECT_LOG $MESSAGE_LOG $DISCONNECT_LOG $CHAT_PROCESSOR_LOG $INDEXER_LOG $SCRAPER_LOG $SCRAPER_WORKER_LOG $GLUE_STARTER_LOG $LAYER_CLEANUP_LOG $KEEP_WARM_LOG"
      ;;
    /aws/lambda/*) echo "$sel" ;;  # already a full log group name
    *) echo "" ;;
  esac
}

# Resolve selected log groups (default to all if none specified)
declare -a LOG_GROUPS=()
if [[ ${#SELECTORS[@]} -eq 0 ]]; then
  LOG_GROUPS=("$CONNECT_LOG" "$MESSAGE_LOG" "$DISCONNECT_LOG" "$CHAT_PROCESSOR_LOG")
else
  for sel in "${SELECTORS[@]}"; do
    lg="$(map_target_to_log_group "$sel")"
    if [[ -n "$lg" ]]; then
      # Expand possible multi group from "all"
      for one in $lg; do
        LOG_GROUPS+=("$one")
      done
    else
      echo "⚠️  Warning: Unknown target '$sel' (supported: connect, message, disconnect, chat or full log group name)"
    fi
  done
  # If nothing resolved, default to all
  if [[ ${#LOG_GROUPS[@]} -eq 0 ]]; then
    LOG_GROUPS=("$CONNECT_LOG" "$MESSAGE_LOG" "$DISCONNECT_LOG" "$CHAT_PROCESSOR_LOG" "$INDEXER_LOG" "$SCRAPER_LOG" "$SCRAPER_WORKER_LOG" "$GLUE_STARTER_LOG" "$LAYER_CLEANUP_LOG" "$KEEP_WARM_LOG")
  fi
fi

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

# Function to follow logs with an initial snapshot to show context
follow_log_group() {
  local log_group=$1
  local prefix=$2
  # Decide snapshot window based on function type
  local snapshot_window="15m"
  case "$log_group" in
    *indexer|*scraper|*scraper_worker|*glue_starter)
      snapshot_window="24h"
      ;;
  esac
  # Show a recent window first to confirm activity
  aws logs tail "$log_group" --since "$snapshot_window" --region "$REGION" --format short 2>&1 | sed "s/^/$prefix/"
  # Follow new logs; avoid stdbuf (problematic on macOS/Homebrew arch); use while-read for prefixing
  aws logs tail "$log_group" --follow --region "$REGION" --format short 2>&1 | while IFS= read -r line; do
    printf "%s%s\n" "$prefix" "$line"
  done &
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
echo "Monitoring log groups:"
for lg in "${LOG_GROUPS[@]}"; do
  echo "  - $lg"
done
echo ""
echo "======================================================================"
echo ""

if [ "$MODE" == "follow" ]; then
    echo "Following logs in real-time (Ctrl+C to stop)..."
    echo "Note: You may not see output until new logs arrive"
    echo ""
  # If a single target is selected, directly exec aws logs tail (best parity with manual usage)
  if [[ ${#LOG_GROUPS[@]} -eq 1 ]]; then
    lg="${LOG_GROUPS[0]}"
    echo "[INFO] Using direct follow for: $lg"
    # Decide snapshot window for direct follow
    SNAPSHOT_WINDOW="15m"
    case "$lg" in
      *indexer|*scraper|*scraper_worker|*glue_starter)
        SNAPSHOT_WINDOW="24h"
        ;;
    esac
    aws logs tail "$lg" --since "$SNAPSHOT_WINDOW" --region "$REGION" --format short
    aws logs tail "$lg" --follow --region "$REGION" --format short
    exit 0
  fi

  # Tail selected logs simultaneously with prefixes inferred from the group name
  for lg in "${LOG_GROUPS[@]}"; do
    prefix="[LOG] "
    case "$lg" in
      *websocket_connect) prefix="[WS-CONNECT] " ;;
      *websocket_message) prefix="[WS-MESSAGE] " ;;
      *websocket_disconnect) prefix="[WS-DISCONNECT] " ;;
      *chat_processor) prefix="[CHAT-PROC] " ;;
      *indexer) prefix="[INDEXER] " ;;
      *scraper_worker) prefix="[SCRAPER-WORKER] " ;;
      *scraper) prefix="[SCRAPER] " ;;
      *glue_starter) prefix="[GLUE] " ;;
      *layer_cleanup) prefix="[LAYER] " ;;
      *keep_websocket_warm) prefix="[KEEP-WARM] " ;;
    esac
    follow_log_group "$lg" "$prefix"
  done
    
    # Wait for all background processes
    wait
else
    echo "Showing recent logs (last $TIME_WINDOW)..."
    echo ""
  for lg in "${LOG_GROUPS[@]}"; do
    case "$lg" in
      *websocket_connect) echo "=== WEBSOCKET CONNECT HANDLER ===" ;;
      *websocket_message) echo "=== WEBSOCKET MESSAGE HANDLER ===" ;;
      *websocket_disconnect) echo "=== WEBSOCKET DISCONNECT HANDLER ===" ;;
      *chat_processor) echo "=== CHAT PROCESSOR HANDLER ===" ;;
      *indexer) echo "=== INDEXER HANDLER ===" ;;
      *scraper) echo "=== SCRAPER HANDLER ===" ;;
      *scraper_worker) echo "=== SCRAPER WORKER HANDLER ===" ;;
      *glue_starter) echo "=== GLUE STARTER HANDLER ===" ;;
      *layer_cleanup) echo "=== LAYER CLEANUP HANDLER ===" ;;
      *keep_websocket_warm) echo "=== KEEP WEBSOCKET WARM HANDLER ===" ;;
      *) echo "=== $lg ===" ;;
    esac
    tail_logs "$lg" ""
    echo ""
  done
    
    echo "💡 Tip: Use './monitor_all_logs.sh recent 1h' to see logs from the last hour"
fi

