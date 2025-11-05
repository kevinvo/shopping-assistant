# Lambda Layer Setup for Chalice

## Building the Layer

The layer is built using Docker. Run:

```bash
cd chalice_app
bash scripts/build-layer.sh
```

This will create the layer in `chalice_app/layer/python/` with all dependencies.

## Chalice Layer Support Research

**Research Results:**
- ❌ Chalice 2.0 does **NOT** support specifying existing layer ARNs in `config.json`
- ✅ Chalice 2.0 has `automatic_layer: true` feature, but this creates a **new managed layer** controlled by Chalice
- ⚠️ Since we use a custom Docker-based layer build process, we cannot use `automatic_layer` (it would conflict with our custom layer)

**Conclusion:** Post-deployment layer attachment is **required** and must be done after every `chalice deploy`.

## Attaching Layer to Chalice Functions

**IMPORTANT:** Layers are NOT preserved during `chalice deploy`. You must attach the layer after every deployment.

### Complete Workflow

1. **Build the layer:**
   ```bash
   cd chalice_app
   bash scripts/build-layer.sh
   ```

2. **Publish layer to AWS:**
   ```bash
   bash scripts/publish-layer.sh shopping-assistant-chalice-layer ap-southeast-1
   ```
   This saves the layer ARN to `.chalice/layer-arn.txt`

3. **Deploy Chalice application:**
   ```bash
   chalice deploy --stage chalice-test
   ```

4. **Attach layer to all functions (REQUIRED after each deploy):**
   ```bash
   bash scripts/post-deploy-attach-layer.sh chalice-test
   ```
   Or use the underlying script directly:
   ```bash
   bash scripts/attach-layer-to-functions.sh
   ```
   The `post-deploy-attach-layer.sh` script is a convenience wrapper that:
   - Reads layer ARN from `.chalice/layer-arn.txt`
   - Calls `attach-layer-to-functions.sh` with proper parameters
   - Provides clear error messages if layer ARN is missing

### Why Post-Deployment Attachment is Required

- Chalice deployments create/update Lambda functions but do not preserve existing layer attachments
- Layers must be manually attached after each `chalice deploy` using `attach-layer-to-functions.sh`
- The layer ARN is automatically read from `.chalice/layer-arn.txt` if not specified

## Current Status
- ✅ Dockerfile created
- ✅ Build script created
- ✅ Publish script created (saves ARN to `.chalice/layer-arn.txt`)
- ✅ Attach script created (reads ARN from `.chalice/layer-arn.txt`)
- ✅ Requirements.txt updated
- ✅ Post-deployment attachment workflow documented

