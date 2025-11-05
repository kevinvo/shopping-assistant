# Chalice Deployment Workflow

This document explains the Python deployment wrapper at
`chalice_app/scripts/deploy_chalice_stage.py`, the supporting scripts it calls,
and the failure modes it protects us from. The goal is to make `chalice deploy`
behave predictably in CI/CD while keeping the API Gateway/WebSocket resources
stable and making sure the environment is healthy after each run.


## High-Level Flow

1. **Load configuration** – reads `.chalice/config.json` for the selected stage
   to resolve environment variables, queue URLs, and deployed-state paths.
2. **Reuse existing SQS event-source mappings** – enumerates the current Lambda →
   SQS mappings and writes them back into `.chalice/deployed/<stage>.json`
   (schema version 2.0) so Chalice updates the existing mapping UUIDs instead of
   creating new ones.
3. **Run `chalice deploy` with retries** – executes `chalice deploy --stage …`
   up to two times, waiting 10 seconds between attempts.
4. **Attach the shared Lambda layer** – calls
   `post_deploy_attach_layer.py` which fetches the latest layer version and
   updates every `shopping-assistant-api-<stage>*` function (with retries and
   verification).
5. **Post-deploy validation** – confirms the chat processor mapping is present
   and enabled, and that the chat-processing SQS backlog is below the configured
   thresholds.


## Edge Cases & Protections

| Problem | Behaviour without the wrapper | How the wrapper fixes it |
|---------|-------------------------------|--------------------------|
| **Chalice re-creates WebSocket/SQS resources** | When `.chalice/deployed/<stage>.json` is missing or downgraded to schema 1.0, Chalice thinks it is a first deploy and issues brand new API Gateway IDs and SQS event-source mappings. | `list_event_mappings()` + `update_deployed_config()` preserve the mapping UUIDs in a schema 2.0 deployed-state file before every deploy, so Chalice updates the existing resources and the WebSocket URL remains stable (`o5b5iy8371…`). |
| **Deployed-state file missing schema fields** | Earlier shell scripts rewrote the JSON without `schema_version`, breaking future deploys. | `load_deployed_state()` seeds default state with `{"schema_version": "2.0", "backend": "api", "resources": []}` when the file does not exist. |
| **`chalice deploy` intermittent failures** | A transient CLI or AWS error aborts the pipeline, leaving resources half-updated (e.g., SQS unsubscribed). | `retry_chalice_deploy()` retries once, logging the attempt number. If the final attempt fails, the wrapper exits non-zero so CI stops. |
| **Shared Lambda layer detaches after deploy** | Chalice does not remember manual layer attachments; each deploy can drop the layer. | `attach_layer()` runs the post-deploy layer script, which updates every function and retries verification via `get-function-configuration`. |
| **Layer attachment script previously broke due to CLI args** | The old shell script passed positional arguments only; converting to Python allows consistent `--stage` and `--region` handling. | The wrapper now invokes `post_deploy_attach_layer.py` directly, which mirrors the same argument names. |
| **SQS backlog masking stuck processors** | Deploy may succeed but the chat processor queue stays full, leading to timeouts. | `ensure_post_deploy()` checks `ApproximateNumberOfMessages` and `ApproximateNumberOfMessagesNotVisible` against configurable thresholds (defaults: 10 visible, 5 in-flight). |
| **Missing configuration values** | Deploy succeeds but the Lambdas fail at runtime (e.g., queue URL not in environment). | `ensure_post_deploy()` raises if `CHAT_PROCESSING_QUEUE_URL` is absent; the wrapper exits non-zero so CI fails visibly. |
| **Layer or mapping validation fails silently** | Without post-checks, we might deploy broken infrastructure and only notice later. | The wrapper exits with status 1 and logs the validation error, stopping CI immediately. |
| **Email alerts for errors** | SNS topic exists but handlers needed explicit wiring. | All Chalice entry points use `@notify_on_exception` (see `error_notifications.py`), which sends stack traces to `ERROR_ALERT_TOPIC_ARN`. |


## Supporting Scripts

- `post_deploy_attach_layer.py`
  - Discovers the latest layer ARN via `list_layer_versions`.
  - Calls `attach_layer_to_functions.py` with retries and verification.
  - Shares the same `--stage/--region` flags as the deploy wrapper.

- `attach_layer_to_functions.py`
  - Lists every Chalice-managed function (`shopping-assistant-api-<stage>*`).
  - Replaces existing versions of the shared layer while preserving any other
    layers already attached.

- `publish_layer.py`
  - Builds and publishes the Chalice layer; used earlier in the pipeline before
    `chalice deploy` runs.


## Configuration & Environment Variables

- `.chalice/config.json` must include:
  - `CHAT_PROCESSING_QUEUE_URL` and `EVALUATION_QUEUE_URL` (per stage).
  - `ERROR_ALERT_TOPIC_ARN` pointing to the SNS topic created in the CDK stack
    (`job-failure-alerts`).
  - Layer bucket settings (`LAYER_*` vars) for the post-deploy cleanup job.

- Threshold overrides:
  - `MAX_VISIBLE_MESSAGES` and `MAX_INFLIGHT_MESSAGES` can be exported in CI (or
    passed as CLI flags) if the defaults are too strict.


## CI/CD Usage

The GitHub Actions workflow calls the wrapper directly:

```yaml
- name: Deploy Chalice stage with validation
  if: github.ref == 'refs/heads/main' && github.event_name == 'push'
  run: |
    python chalice_app/scripts/deploy_chalice_stage.py \
      --stage chalice-test \
      --region \
      ${{ env.AWS_REGION }}
```

If the wrapper exits non-zero, subsequent steps (integration tests, etc.) do not
run, signalling the failure immediately.


## Local Usage

From the project root:

```bash
python chalice_app/scripts/deploy_chalice_stage.py \
  --stage chalice-test \
  --region ap-southeast-1
```

You can add `--max-attempts`, `--max-visible`, or `--max-inflight` flags if you
need non-default behaviour during troubleshooting.


## Future Enhancements

- Surface a concise summary (e.g., WebSocket URL, mapping IDs) after successful
  deploys for easier log scanning.
- Optionally add an Insights query link to the SNS error emails so they link
  directly to the relevant CloudWatch logs.
- Extend the wrapper to verify DynamoDB tables or other dependencies if needed.

For now, the wrapper guards against the main Chalice edge cases we’ve observed:
unintended API recreation, missing layers, silent queue backlogs, and transient
deploy failures. Checking in this document ensures future contributors
understand the rationale and behaviour of the script.

