#!/usr/bin/env python3
"""
Test script to verify layer dependencies can be imported.
Run this after building and attaching the layer to verify imports work.
"""
import os
import sys


def test_layer_imports():
    """Test that critical layer dependencies can be imported"""
    print("Testing layer dependency imports...")
    print("=" * 70)

    failures = []

    # Critical dependencies to test
    dependencies = [
        ("pydantic_core._pydantic_core", "pydantic_core"),
        ("pandas", "pandas"),
        ("numpy", "numpy"),
        ("langchain", "langchain"),
        ("langchain_core", "langchain_core"),
        ("qdrant_client", "qdrant_client"),
        ("anthropic", "anthropic"),
        ("tiktoken", "tiktoken"),
    ]

    for module_name, display_name in dependencies:
        try:
            __import__(module_name)
            print(f"✅ {display_name}")
        except ImportError as e:
            print(f"❌ {display_name}: {e}")
            failures.append(display_name)

    print("=" * 70)

    if failures:
        print(f"❌ Failed to import: {', '.join(failures)}")
        print("\nPossible issues:")
        print("  1. Layer not built: Run 'bash scripts/build-layer.sh'")
        print("  2. Layer not attached to Lambda function")
        print("  3. Layer path not in PYTHONPATH")
        return False
    print("✅ All dependencies imported successfully!")
    return True


if __name__ == "__main__":
    # Try to add layer to path if it exists locally
    layer_path = os.path.join(os.path.dirname(__file__), "..", "layer", "python")
    if os.path.exists(layer_path):
        sys.path.insert(0, layer_path)
        print(f"Added local layer path: {layer_path}")
        print()

    success = test_layer_imports()
    sys.exit(0 if success else 1)
