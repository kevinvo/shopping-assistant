# Use Amazon Linux 2023 to match Lambda Python 3.12 runtime environment
# Lambda Python 3.12 runs on Amazon Linux 2023, so building here ensures compatibility
FROM amazonlinux:2023 AS builder

# Install Python 3.12 and build dependencies
RUN dnf install -y \
    python3.12 \
    python3.12-devel \
    python3.12-pip \
    gcc \
    gcc-c++ \
    zlib-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Create symlinks for python and pip to use Python 3.12
RUN ln -sf /usr/bin/python3.12 /usr/bin/python && \
    ln -sf /usr/bin/pip3.12 /usr/bin/pip

WORKDIR /lambda-layer

# Copy requirements first
# NOTE: Using root requirements.txt (copied from lambda/ during migration)
COPY requirements.txt requirements.txt

# Install numpy separately first to ensure proper installation
RUN pip install \
    --platform manylinux2014_x86_64 \
    --target python \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    --no-cache-dir \
    numpy==1.26.4

# Install qdrant-client directly in the python directory without cleanup
RUN pip install \
    --target python \
    --no-cache-dir \
    portalocker==2.8.2 \
    qdrant-client==1.13.3

# Install pydantic and pydantic-core FIRST to ensure compatibility
# Langsmith requires pydantic>=1,<3 which resolves to pydantic 2.12.4 (requires pydantic-core 2.41.5)
# Let pip resolve the compatible version automatically based on langsmith requirements
RUN pip install \
    --target python \
    --no-cache-dir \
    pydantic && \
    if [ -f python/pydantic_core/_pydantic_core.cpython-312-x86_64-linux-gnu.so ] || \
       [ -f python/pydantic_core/_pydantic_core.cpython-312-linux-x86_64.so ] || \
       find python/pydantic_core -name "_pydantic_core*.so" 2>/dev/null | grep -q .; then \
        echo "✅ pydantic_core binary extension found"; \
    else \
        echo "⚠️  Warning: pydantic_core binary extension not found"; \
        ls -la python/pydantic_core/ 2>/dev/null | head -20; \
        exit 1; \
    fi

# Install other packages (with binary-only for most packages)
# Use --no-deps for langchain-core to prevent it from upgrading pydantic
RUN pip install \
    --platform manylinux2014_x86_64 \
    --target python \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    --no-cache-dir \
    pandas==2.2.0 \
    tiktoken==0.5.2 \
    regex==2023.12.25 && \
    pip install \
    --target python \
    --no-cache-dir \
    --no-deps \
    langchain-core==0.3.76 \
    langchain==0.3.27 && \
    # Ensure pydantic and pydantic-core are compatible (langsmith will install 2.12.4 + 2.41.5)
    # No need to reinstall - langsmith will install the correct versions
    pip install \
    --target python \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    --no-cache-dir \
    -r requirements.txt && \
    # Reinstall pydantic and pydantic-core to ensure correct versions after requirements.txt
    # Remove ALL pydantic installations completely (both dist-info and package directories)
    # This ensures we start fresh and install the correct version
    # Ensure pydantic and pydantic-core versions are compatible after requirements.txt
    # Langsmith will have installed pydantic 2.12.4 + pydantic-core 2.41.5, which is correct
    # Just verify the binary extension is present
    # Verify pydantic-core binary extension is still present
    if [ -f python/pydantic_core/_pydantic_core.cpython-312-x86_64-linux-gnu.so ] || \
       [ -f python/pydantic_core/_pydantic_core.cpython-312-linux-x86_64.so ] || \
       find python/pydantic_core -name "_pydantic_core*.so" 2>/dev/null | grep -q .; then \
        echo "✅ pydantic_core binary extension verified after requirements.txt"; \
    else \
        echo "❌ ERROR: pydantic_core binary extension missing after requirements.txt"; \
        ls -la python/pydantic_core/ 2>/dev/null | head -20; \
        exit 1; \
    fi

# Cleanup and optimization (excluding qdrant-client, portalocker, and pydantic_core)
RUN cd python/ && \
    find . -type d -name "tests" -not -path "*/anyio/*" -not -path "*/langchain*/*" -not -path "*/numpy/*" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type d -name "__pycache__" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type f -name "*.pyc" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.pyo" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.pyd" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type d -name "*.dist-info" -not -name "qdrant_client*.dist-info" -not -name "portalocker*.dist-info" -not -name "pydantic_core*.dist-info" -exec rm -rf {} + && \
    find . -type d -name "*.egg-info" -not -name "qdrant_client*.egg-info" -not -name "portalocker*.egg-info" -not -name "pydantic_core*.egg-info" -exec rm -rf {} + && \
    find . -type f -name "*.md" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.txt" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.h" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.c" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.cpp" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.html" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.rst" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    # Remove pandas directories that aren't needed
    rm -rf pandas/tests && \
    rm -rf pandas/doc && \
    rm -rf pandas/io/formats/templates && \
    rm -rf pandas/_libs/tslibs/src && \
    rm -rf pandas/_libs/src && \
    rm -rf pandas/util && \
    rm -rf pandas/plotting && \
    rm -rf pandas/io/clipboard && \
    rm -rf pandas/io/excel && \
    rm -rf pandas/io/sas && \
    rm -rf pandas/io/spss && \
    rm -rf pandas/io/stata && \
    rm -rf pandas/io/parquet && \
    rm -rf pandas/io/feather && \
    rm -rf pandas/io/json && \
    rm -rf pandas/io/formats && \
    rm -rf pandas/io/pickle && \
    rm -rf pandas/io/xml && \
    rm -rf pandas/io/sql && \
    rm -rf pandas/arrays && \
    rm -rf pandas/core/computation && \
    rm -rf pandas/core/reshape && \
    rm -rf pandas/core/tools && \
    rm -rf pandas/core/window

# Final cleanup (excluding qdrant-client, portalocker, and pydantic_core)
RUN cd python/ && \
    find . -type f -name "*test*.py" -not -path "*/anyio/*" -not -path "*/numpy/*" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type d -name "*test*" -not -path "*/anyio/*" -not -path "*/numpy/*" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type f -name "*.pxi" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.pxd" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.pyx" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type f -name "*.ipynb" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -delete && \
    find . -type d -name "examples" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type d -name "demo" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type d -name "scripts" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type d -name ".github" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} + && \
    find . -type d -name ".pytest_cache" -not -path "*/qdrant_client*/*" -not -path "*/portalocker*/*" -not -path "*/pydantic_core*/*" -exec rm -rf {} +

# Final stage
FROM busybox:latest
WORKDIR /lambda-layer
COPY --from=builder /lambda-layer/python /lambda-layer/python

CMD cp -r python/. /output/