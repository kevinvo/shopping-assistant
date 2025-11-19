#!/bin/bash
# Monitor Lambda logs
# Usage:
#   ./monitor_all_logs.sh [follow|recent|analyze] [time_window] [targets...]
#     follow        Follow logs in real-time (shows last 15m snapshot first; 24h for daily jobs)
#     recent        Print recent logs once (default 5m; pass 15m/1h/24h/etc.)
#     analyze       Print counts (invocations/timeouts/errors) over a window (default 24h for daily jobs, else 15m)
#     time_window   Optional for 'recent' and 'analyze'; if omitted, defaults apply as above.
#     targets       Optional list of which logs to include. Supported aliases:
#                     connect, message, disconnect, chat, indexer, scraper, scraper_worker, glue_starter, layer_cleanup, keep_warm
#                   Or pass full log group names to tail directly.
# Environment:
#   SCRAPER_STATE_MACHINE_ARN  (optional) State Machine ARN to analyze Step Functions timeouts for Scraper Trigger
#
# Examples:
#   ./monitor_all_logs.sh follow
#   ./monitor_all_logs.sh recent 15m connect message
#   ./monitor_all_logs.sh analyze 24h indexer scraper_worker
#   ./monitor_all_logs.sh follow /aws/lambda/shopping-assistant-api-chalice-test-websocket_connect

MODE=${1:-follow}
TIME_WINDOW=${2:-5m}
# For analyze mode, prefer a longer default window
if [[ "$MODE" == "analyze" && "$TIME_WINDOW" == "5m" ]]; then
  TIME_WINDOW="24h"
fi
REGION="ap-southeast-1"

# Track background process IDs for cleanup
declare -a BACKGROUND_PIDS=()

# Cleanup function to kill all background processes
cleanup() {
  echo ""
  echo "Stopping log monitoring..."
  # Kill all background processes we spawned
  for pid in "${BACKGROUND_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      # Kill the process and its process group (children)
      kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  # Kill any remaining background jobs
  jobs -p | while read -r job_pid; do
    kill -TERM "$job_pid" 2>/dev/null || true
  done
  # Kill any remaining aws logs tail processes that are children of this script
  pkill -P $$ -f "aws logs tail.*--follow" 2>/dev/null || true
  # Wait a moment for processes to terminate
  sleep 0.2
  # Force kill any remaining processes
  for pid in "${BACKGROUND_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  jobs -p | while read -r job_pid; do
    kill -KILL "$job_pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "Stopped."
  exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM EXIT

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
  echo "Select which functions to tail (you can enter multiple, e.g. '4,5,7' or type '11' for all):"
  echo "  1) WebSocket Connect"
  echo "  2) WebSocket Message"
  echo "  3) WebSocket Disconnect"
  echo "  4) Chat Processor"
  echo "  5) Indexer"
  echo "  6) Scraper Trigger (Step Functions)"
  echo "  7) Scraper Worker (Lambda)"
  echo "  8) Glue Starter"
  echo "  9) Layer Cleanup"
  echo " 10) Keep WebSocket Warm"
  echo " 11) All of the above"
  echo " 12) Analyze: Scraper Trigger + Scraper Worker (24h)"
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
        12) SELECTORS+=("ANALYZE_SCRAPER_BUNDLE") ;;
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

# Special option: analyze bundle for scraper trigger + worker
for s in "${SELECTORS[@]}"; do
  if [[ "$s" == "ANALYZE_SCRAPER_BUNDLE" ]]; then
    MODE="analyze"
    # Default to 24h if user didn't provide explicit window param
    if [[ -z "$2" ]]; then
      TIME_WINDOW="24h"
    fi
    SELECTORS=("scraper" "scraper_worker")
    break
  fi
done

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

# ANSI color codes (using $'...' syntax to interpret escape sequences)
COLOR_RESET=$'\033[0m'
COLOR_RED=$'\033[0;31m'
COLOR_GREEN=$'\033[0;32m'
COLOR_YELLOW=$'\033[0;33m'
COLOR_BLUE=$'\033[0;34m'
COLOR_MAGENTA=$'\033[0;35m'
COLOR_CYAN=$'\033[0;36m'
COLOR_BRIGHT_RED=$'\033[1;31m'
COLOR_BRIGHT_GREEN=$'\033[1;32m'
COLOR_BRIGHT_YELLOW=$'\033[1;33m'
COLOR_BRIGHT_BLUE=$'\033[1;34m'
COLOR_BRIGHT_MAGENTA=$'\033[1;35m'
COLOR_BRIGHT_CYAN=$'\033[1;36m'

# Function to get color code for a log group
get_log_color() {
  local log_group=$1
  case "$log_group" in
    *websocket_connect) echo "$COLOR_CYAN" ;;
    *websocket_message) echo "$COLOR_GREEN" ;;
    *websocket_disconnect) echo "$COLOR_YELLOW" ;;
    *chat_processor) echo "$COLOR_MAGENTA" ;;
    *indexer) echo "$COLOR_BLUE" ;;
    *scraper_worker) echo "$COLOR_BRIGHT_RED" ;;
    *scraper) echo "$COLOR_RED" ;;
    *glue_starter) echo "$COLOR_BRIGHT_CYAN" ;;
    *layer_cleanup) echo "$COLOR_BRIGHT_YELLOW" ;;
    *keep_websocket_warm) echo "$COLOR_BRIGHT_GREEN" ;;
    *) echo "$COLOR_RESET" ;;
  esac
}

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
  local color=$(get_log_color "$log_group")
  local reset="$COLOR_RESET"
  # Decide snapshot window based on function type
  local snapshot_window="15m"
  case "$log_group" in
    *indexer|*scraper|*scraper_worker|*glue_starter)
      snapshot_window="24h"
      ;;
    *layer_cleanup)
      snapshot_window="14d"
      ;;
  esac
  # Show a recent window first to confirm activity
  aws logs tail "$log_group" --since "$snapshot_window" --region "$REGION" --format short 2>&1 | while IFS= read -r line; do
    printf "%b%s%b%s\n" "$color" "$prefix" "$reset" "$line"
  done
  # Follow new logs; avoid stdbuf (problematic on macOS/Homebrew arch); use while-read for prefixing
  # Run in background and track PID for cleanup
  (
    aws logs tail "$log_group" --follow --region "$REGION" --format short 2>&1 | while IFS= read -r line; do
      printf "%b%s%b%s\n" "$color" "$prefix" "$reset" "$line"
    done
  ) &
  local bg_pid=$!
  BACKGROUND_PIDS+=("$bg_pid")
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
  # If a single target is selected, use prefixing for consistency
  if [[ ${#LOG_GROUPS[@]} -eq 1 ]]; then
    lg="${LOG_GROUPS[0]}"
    echo "[INFO] Following logs for: $lg"
    # Decide snapshot window for direct follow
    SNAPSHOT_WINDOW="15m"
    case "$lg" in
      *indexer|*scraper|*scraper_worker|*glue_starter)
        SNAPSHOT_WINDOW="24h"
        ;;
      *layer_cleanup)
        SNAPSHOT_WINDOW="14d"
        ;;
    esac
    # Determine prefix
    prefix="[LOG] "
    case "$lg" in
      *websocket_connect) prefix="[WS-CONNECT] " ;;
      *websocket_message) prefix="[WS-MESSAGE] " ;;
      *websocket_disconnect) prefix="[WS-DISCONNECT] " ;;
      *chat_processor) prefix="[CHAT-PROC] " ;;
      *indexer) prefix="[INDEXER] " ;;
      *scraper_worker) prefix="[SCRAPER-WORKER] " ;;
      *scraper) prefix="[SCRAPER-TRIGGER] " ;;
      *glue_starter) prefix="[GLUE] " ;;
      *layer_cleanup) prefix="[LAYER] " ;;
      *keep_websocket_warm) prefix="[KEEP-WARM] " ;;
    esac
    color=$(get_log_color "$lg")
    reset="$COLOR_RESET"
    # Show snapshot with prefix
    aws logs tail "$lg" --since "$SNAPSHOT_WINDOW" --region "$REGION" --format short 2>&1 | while IFS= read -r line; do
      printf "%b%s%b%s\n" "$color" "$prefix" "$reset" "$line"
    done
    # Follow with prefix
    aws logs tail "$lg" --follow --region "$REGION" --format short 2>&1 | while IFS= read -r line; do
      printf "%b%s%b%s\n" "$color" "$prefix" "$reset" "$line"
    done
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
      *scraper) prefix="[SCRAPER-TRIGGER] " ;;
      *glue_starter) prefix="[GLUE] " ;;
      *layer_cleanup) prefix="[LAYER] " ;;
      *keep_websocket_warm) prefix="[KEEP-WARM] " ;;
    esac
    # Color is applied inside follow_log_group function
    follow_log_group "$lg" "$prefix"
  done
    
    # Wait for all background processes
    wait
elif [ "$MODE" == "analyze" ]; then
  echo "Analyzing logs for selected groups..."
  echo ""
  for lg in "${LOG_GROUPS[@]}"; do
    # Determine label
    label="$lg"
    case "$lg" in
      *websocket_connect)   label="WEBSOCKET CONNECT" ;;
      *websocket_message)   label="WEBSOCKET MESSAGE" ;;
      *websocket_disconnect) label="WEBSOCKET DISCONNECT" ;;
      *chat_processor)      label="CHAT PROCESSOR" ;;
      *indexer)             label="INDEXER (daily)" ;;
      *scraper)             label="SCRAPER TRIGGER (Step Functions, daily)" ;;
      *scraper_worker)      label="SCRAPER WORKER (Lambda, invoked by Step Functions)" ;;
      *glue_starter)        label="GLUE STARTER (daily)" ;;
      *layer_cleanup)       label="LAYER CLEANUP" ;;
      *keep_websocket_warm) label="KEEP WEBSOCKET WARM" ;;
    esac
    # Determine window per group if user left default 24h/15m behavior
    SNAPSHOT_WINDOW="$TIME_WINDOW"
    if [[ -z "$2" ]]; then
      # No explicit window passed; choose defaults: 24h for daily jobs, else 15m
      case "$lg" in
        *indexer|*scraper|*scraper_worker|*glue_starter) SNAPSHOT_WINDOW="24h" ;;
        *layer_cleanup) SNAPSHOT_WINDOW="14d" ;;
        *) SNAPSHOT_WINDOW="15m" ;;
      esac
    fi
    color=$(get_log_color "$lg")
    echo -e "${color}=== $label ===${COLOR_RESET}"
    echo "Window: $SNAPSHOT_WINDOW"
    tmpfile="$(mktemp)"
    if ! aws logs tail "$lg" --since "$SNAPSHOT_WINDOW" --region "$REGION" --format short >"$tmpfile" 2>/dev/null; then
      echo "  ❌ Unable to read logs (check permissions or log group)."
      rm -f "$tmpfile"
      echo ""
      continue
    fi
    total_lines=$(wc -l < "$tmpfile" | tr -d ' ')
    starts=$(grep -E -c "START[[:space:]]+RequestId" "$tmpfile" || true)
    timeouts=$(grep -E -ci "Task timed out" "$tmpfile" || true)
    errors=$(grep -E -ci "ERROR|Error|Exception" "$tmpfile" || true)
    echo "  Lines: $total_lines"
    echo "  Invocations (START): $starts"
    echo "  Timeouts: $timeouts"
    echo "  Error lines (ERROR/Exception): $errors"
    if [[ "$starts" -gt 0 ]]; then
      echo "  Recent invocation timestamps (UTC):"
      grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2}T" "$tmpfile" | grep -E "START[[:space:]]+RequestId" | tail -5 | awk '{print $1}' | sed 's/T/ /' | sed 's/\\..*$//'
      case "$lg" in
        *scraper_worker)
          echo "  Note: Step Functions retries on timeout (up to 3 attempts) — expect up to 3 START lines per timed-out run."
          ;;
      esac
    fi
    # If this is the Scraper Trigger, optionally include Step Functions timeout counts when ARN is provided
    if [[ "$lg" == *scraper && -n "$SCRAPER_STATE_MACHINE_ARN" ]]; then
      # Attempt to count TIMED_OUT executions in the last 24h (best-effort, limited by --max-results)
      # Compute ISO start time 24h ago (UTC) in a portable way via Python if available
      if command -v python3 >/dev/null 2>&1; then
        START_ISO=$(python3 - <<'PY'
import datetime, sys
print((datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)
      else
        # Fallback: leave empty to not filter by time
        START_ISO=""
      fi
      # Fetch TIMED_OUT executions
      if [[ -n "$START_ISO" ]]; then
        sf_count=$(aws stepfunctions list-executions --state-machine-arn "$SCRAPER_STATE_MACHINE_ARN" --status-filter TIMED_OUT --max-results 100 --query "length(executions[?startDate>=\`$START_ISO\`])" --output text 2>/dev/null || echo "N/A")
      else
        sf_count=$(aws stepfunctions list-executions --state-machine-arn "$SCRAPER_STATE_MACHINE_ARN" --status-filter TIMED_OUT --max-results 100 --query "length(executions)" --output text 2>/dev/null || echo "N/A")
      fi
      echo "  Step Functions TIMED_OUT executions (last 24h): $sf_count"
    elif [[ "$lg" == *scraper && -z "$SCRAPER_STATE_MACHINE_ARN" ]]; then
      echo "  Tip: set SCRAPER_STATE_MACHINE_ARN to also report Step Functions timeout counts."
    fi
    rm -f "$tmpfile"
    echo ""
  done
else
    echo "Showing recent logs (last $TIME_WINDOW)..."
    echo ""
  for lg in "${LOG_GROUPS[@]}"; do
    color=$(get_log_color "$lg")
    case "$lg" in
      *websocket_connect) echo -e "${color}=== WEBSOCKET CONNECT HANDLER ===${COLOR_RESET}" ;;
      *websocket_message) echo -e "${color}=== WEBSOCKET MESSAGE HANDLER ===${COLOR_RESET}" ;;
      *websocket_disconnect) echo -e "${color}=== WEBSOCKET DISCONNECT HANDLER ===${COLOR_RESET}" ;;
      *chat_processor) echo -e "${color}=== CHAT PROCESSOR HANDLER ===${COLOR_RESET}" ;;
      *indexer) echo -e "${color}=== INDEXER HANDLER ===${COLOR_RESET}" ;;
      *scraper) echo -e "${color}=== SCRAPER TRIGGER (STEP FUNCTIONS) ===${COLOR_RESET}" ;;
      *scraper_worker) echo -e "${color}=== SCRAPER WORKER (LAMBDA) ===${COLOR_RESET}" ;;
      *glue_starter) echo -e "${color}=== GLUE STARTER HANDLER ===${COLOR_RESET}" ;;
      *layer_cleanup) echo -e "${color}=== LAYER CLEANUP HANDLER ===${COLOR_RESET}" ;;
      *keep_websocket_warm) echo -e "${color}=== KEEP WEBSOCKET WARM HANDLER ===${COLOR_RESET}" ;;
      *) echo -e "${color}=== $lg ===${COLOR_RESET}" ;;
    esac
    tail_logs "$lg" ""
    echo ""
  done
    
    echo "💡 Tip: Use './monitor_all_logs.sh recent 1h' to see logs from the last hour"
fi

