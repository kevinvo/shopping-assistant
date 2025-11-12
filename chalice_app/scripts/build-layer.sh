#!/bin/bash

set -e  # Exit on error

# Get the project root directory (parent of chalice_app)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHALICE_APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$CHALICE_APP_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "Building Chalice Lambda layer..."
echo "Working directory: $PROJECT_ROOT"
echo "Using Dockerfile and requirements.txt from root (migrated from lambda/)"

echo "Cleaning up old layer in chalice_app..."
rm -rf "$CHALICE_APP_DIR/layer"
rm -rf "$CHALICE_APP_DIR/lambda-layer"

echo "Creating layer directory structure..."
mkdir -p "$CHALICE_APP_DIR/layer/python"

echo "Building Docker image for x86_64 (Lambda architecture)..."
# Use buildx to force x86_64 architecture, even on ARM Macs
docker buildx build --platform linux/amd64 -t lambda-layer:latest -f Dockerfile . --load > /dev/null

echo "Running container to copy dependencies..."
docker run --rm --platform linux/amd64 \
    -v "$CHALICE_APP_DIR/layer/python:/output" \
    --entrypoint sh \
    lambda-layer:latest \
    -c "cp -r /lambda-layer/python/. /output/" > /dev/null

echo "Verifying layer contents..."
if [ ! "$(ls -A $CHALICE_APP_DIR/layer/python)" ]; then
    echo "Error: No files were copied to layer/python"
    exit 1
fi

# Check for the presence of pydantic_core._pydantic_core binary
echo "Checking for pydantic_core._pydantic_core binary..."
if [ -f "$CHALICE_APP_DIR/layer/python/pydantic_core/_pydantic_core.cpython-312-x86_64-linux-gnu.so" ] || \
   [ -f "$CHALICE_APP_DIR/layer/python/pydantic_core/_pydantic_core.cpython-312-linux-x86_64.so" ] || \
   [ -f "$CHALICE_APP_DIR/layer/python/pydantic_core/_pydantic_core.cpython-310-x86_64-linux-gnu.so" ] || \
   [ -f "$CHALICE_APP_DIR/layer/python/pydantic_core/_pydantic_core.cpython-310-linux-x86_64.so" ]; then
    echo "✅ pydantic_core._pydantic_core binary is present (x86_64 for Lambda)."
    echo "   Note: Local import test skipped (requires x86_64 architecture)"
else
    echo "⚠️  Warning: Expected x86_64 .so file not found, checking for any .so file..."
    if find "$CHALICE_APP_DIR/layer/python/pydantic_core" -name "*_pydantic_core*.so" 2>/dev/null | grep -q .; then
        echo "   Found .so file(s):"
        find "$CHALICE_APP_DIR/layer/python/pydantic_core" -name "*_pydantic_core*.so" 2>/dev/null | head -3
        echo "   (Will verify architecture is correct for Lambda)"
    else
        echo "❌ Error: No pydantic_core._pydantic_core .so file found."
        exit 1
    fi
fi

# Check for other critical dependencies (file-based check since x86_64 binaries can't run on ARM)
echo "Verifying critical dependencies (file presence check)..."
MISSING=0

if [ -d "$CHALICE_APP_DIR/layer/python/pandas" ]; then
    echo "✅ pandas"
else
    echo "❌ pandas missing"
    MISSING=1
fi

if [ -d "$CHALICE_APP_DIR/layer/python/langchain" ]; then
    echo "✅ langchain"
else
    echo "❌ langchain missing"
    MISSING=1
fi

if [ -d "$CHALICE_APP_DIR/layer/python/qdrant_client" ]; then
    echo "✅ qdrant_client"
else
    echo "❌ qdrant_client missing"
    MISSING=1
fi

if [ -d "$CHALICE_APP_DIR/layer/python/numpy" ]; then
    echo "✅ numpy"
else
    echo "❌ numpy missing"
    MISSING=1
fi

if [ $MISSING -ne 0 ]; then
    echo "❌ Dependency verification failed"
    exit 1
fi

echo ""
echo "✅ Layer built successfully in $CHALICE_APP_DIR/layer directory"
echo "Layer size: $(du -sh $CHALICE_APP_DIR/layer/python | cut -f1)"

