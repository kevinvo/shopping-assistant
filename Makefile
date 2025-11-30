.ONESHELL:
SHELL := /bin/bash

.PHONY: help quality format format-check lint lint-fix type-check security test test-cov syntax-check install-dev clean

# Directories to check
PYTHON_DIRS := chalice_app/ cdk_infrastructure/ glue_jobs/
PYTHON_FILES := $(shell find $(PYTHON_DIRS) -name "*.py" -not -path "*/venv/*" -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -path "*/layer/*" -not -path "*/cdk.out/*" -not -path "*/node_modules/*")

# Activate virtual environment if it exists
ACTIVATE_VENV := if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi;

# Default target
help:
	@echo "Available targets:"
	@echo "  make quality      - Run all quality checks (format, lint, type-check, security, test, syntax)"
	@echo "  make format       - Format code with Black"
	@echo "  make format-check - Check code formatting without modifying files (Black)"
	@echo "  make lint         - Run Ruff linter"
	@echo "  make lint-fix     - Run Ruff linter and auto-fix issues"
	@echo "  make type-check   - Run MyPy type checker"
	@echo "  make security     - Run Bandit security checker"
	@echo "  make test         - Run pytest tests"
	@echo "  make test-cov     - Run pytest tests with coverage"
	@echo "  make syntax-check - Check Python syntax"
	@echo "  make install-dev  - Install development dependencies"
	@echo "  make clean        - Clean Python cache files"

# Run all quality checks (auto-fixes formatting)
quality: format lint type-check security test syntax-check
	@echo ""
	@echo "✅ All quality checks passed!"

# Format code with Black
format:
	@$(ACTIVATE_VENV) \
	echo "🔧 Formatting code with Black..." && \
	black $(PYTHON_DIRS)

# Check code formatting without modifying files
format-check:
	@$(ACTIVATE_VENV) \
	echo "🔍 Checking code formatting with Black..." && \
	black --check $(PYTHON_DIRS)

# Run Ruff linter
lint:
	@$(ACTIVATE_VENV) \
	echo "🔍 Running Ruff linter..." && \
	ruff check $(PYTHON_DIRS)

# Run Ruff linter and auto-fix issues
lint-fix:
	@$(ACTIVATE_VENV) \
	echo "🔧 Running Ruff linter with auto-fix..." && \
	ruff check --fix $(PYTHON_DIRS)

# Run MyPy type checker
type-check:
	@$(ACTIVATE_VENV) \
	echo "🔍 Running MyPy type checker..." && \
	mypy $(PYTHON_DIRS) || echo "⚠️  MyPy found type issues (non-blocking)"

# Run Bandit security checker
security:
	@$(ACTIVATE_VENV) \
	echo "🔍 Running Bandit security checker..." && \
	bandit -r $(PYTHON_DIRS) -ll

# Run pytest tests
test:
	@$(ACTIVATE_VENV) \
	echo "🧪 Running pytest tests..." && \
	pytest -v --tb=short chalice_app/tests

# Run pytest tests with coverage
test-cov:
	@$(ACTIVATE_VENV) \
	echo "🧪 Running pytest tests with coverage..." && \
	pytest -v --tb=short --cov=chalice_app --cov-report=term-missing chalice_app/tests

# Check Python syntax
syntax-check:
	@$(ACTIVATE_VENV) \
	echo "🔍 Checking Python syntax..." && \
	python -m py_compile $(PYTHON_FILES) || (echo "❌ Syntax errors found!" && exit 1) && \
	echo "✅ All Python files have valid syntax"

# Install development dependencies
install-dev:
	@$(ACTIVATE_VENV) \
	echo "📦 Installing development dependencies..." && \
	pip install --upgrade pip && \
	pip install -r requirements_dev.txt && \
	pip install -r requirements.txt

# Clean Python cache files
clean:
	@echo "🧹 Cleaning Python cache files..."
	find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	@echo "✅ Cleanup complete"

