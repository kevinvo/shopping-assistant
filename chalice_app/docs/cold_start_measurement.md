# Cold Start Latency Measurement

This document describes how to measure and analyze cold start latency for Lambda functions in this project.

## Overview

Cold start latency is the time it takes for a Lambda function to initialize when a new container is created. This includes:
- Container initialization
- Runtime initialization
- Module imports
- Application initialization

## Components

### 1. Cold Start Detection Module (`chalicelib/core/cold_start.py`)

Provides utilities to detect and measure cold starts:
- `is_cold_start()`: Detects if this is the first invocation in a container
- `measure_cold_start()`: Decorator to automatically measure cold start latency
- `log_cold_start_metrics()`: Logs structured metrics to CloudWatch

### 2. Integration in `app.py`

The main application file has been updated to:
- Mark initialization start/end times
- Apply cold start measurement decorator to Lambda handlers

### 3. Analysis Script (`scripts/analyze_cold_starts.py`)

Python script to query CloudWatch Logs and analyze cold start metrics.

### 4. Convenience Script (`scripts/measure_cold_start.sh`)

Bash script to force a cold start and measure latency.

## Usage

### Measuring Cold Start Latency

#### Option 1: Using the Convenience Script

```bash
cd chalice_app
./scripts/measure_cold_start.sh scraper_worker chalice-test ap-southeast-1
```

This script will:
1. Wait for the container to expire (optional)
2. Invoke the Lambda function
3. Analyze the logs to extract cold start metrics

#### Option 2: Manual Measurement

1. **Wait for container expiration** (15+ minutes of inactivity)
2. **Invoke the function**:
   ```bash
   aws lambda invoke \
     --function-name shopping-assistant-api-chalice-test-scraper_worker \
     --region ap-southeast-1 \
     --payload '{}' \
     /tmp/response.json
   ```

3. **Analyze logs**:
   ```bash
   python3 scripts/analyze_cold_starts.py \
     --function-name shopping-assistant-api-chalice-test-scraper_worker \
     --stage chalice-test \
     --hours 1
   ```

### Analyzing Historical Data

To analyze cold start metrics over a period of time:

```bash
python3 scripts/analyze_cold_starts.py \
  --function-name shopping-assistant-api-chalice-test-scraper_worker \
  --stage chalice-test \
  --hours 24 \
  --output table
```

Output formats:
- `table`: Human-readable table format (default)
- `json`: JSON output for programmatic use
- `summary`: Brief summary statistics

### Viewing Metrics in CloudWatch Logs

Cold start metrics are logged with the prefix `COLD_START_METRICS`. You can filter logs in CloudWatch:

1. Go to CloudWatch Logs
2. Select the log group: `/aws/lambda/<function-name>`
3. Filter by: `COLD_START_METRICS` or `🚀` (cold start) or `⚡` (warm start)

## Metrics Collected

The following metrics are logged for each invocation:

- `is_cold_start`: Boolean indicating if this is a cold start
- `handler_name`: Name of the Lambda handler
- `request_id`: AWS request ID
- `function_name`: Lambda function name
- `function_version`: Function version
- `memory_limit_mb`: Allocated memory
- `init_duration_ms`: Initialization duration (cold starts only)
- `handler_duration_ms`: Handler execution duration
- `remaining_time_ms`: Remaining execution time

## Understanding Results

### Cold Start Rate

The cold start rate indicates how often containers are being reused:
- **High rate (>50%)**: Containers are expiring frequently, possibly due to low traffic
- **Low rate (<10%)**: Good container reuse, warm starts are common

### Initialization Duration

Typical initialization durations:
- **<100ms**: Excellent (minimal dependencies)
- **100-500ms**: Good (moderate dependencies)
- **500-1000ms**: Acceptable (many dependencies or large layers)
- **>1000ms**: Consider optimization (very large dependencies or layers)

### Percentiles

- **P50 (median)**: Typical cold start time
- **P95**: 95% of cold starts are faster than this
- **P99**: 99% of cold starts are faster than this

## Optimization Tips

If cold start latency is high, consider:

1. **Reduce dependencies**: Minimize the number and size of imported packages
2. **Optimize Lambda layers**: Keep layers small and focused
3. **Lazy imports**: Import heavy dependencies only when needed
4. **Provisioned concurrency**: Keep containers warm for critical functions
5. **Increase memory**: More memory = faster CPU = faster initialization

## Example Output

```
================================================================================
COLD START ANALYSIS
================================================================================

Total Invocations: 150
Cold Starts: 12
Warm Starts: 138
Cold Start Rate: 8.00%

--------------------------------------------------------------------------------
Initialization Duration (ms)
--------------------------------------------------------------------------------
  Min:    234.50
  Max:    567.23
  Avg:    345.67
  P50:    332.10
  P95:    512.45
  P99:    545.89

================================================================================

Recent Cold Starts:
--------------------------------------------------------------------------------
  2024-01-15 10:23:45 | Init: 345.67ms
  2024-01-15 08:12:30 | Init: 234.50ms
  ...
```

## Troubleshooting

### No metrics found

- Ensure the function has been invoked recently
- Check that logs are being written to CloudWatch
- Verify the function name is correct

### Metrics show 0% cold start rate

- The function may be invoked very frequently (containers stay warm)
- Wait longer between invocations to force a cold start

### High cold start latency

- Check Lambda layer size
- Review imported dependencies
- Consider using provisioned concurrency for critical paths

