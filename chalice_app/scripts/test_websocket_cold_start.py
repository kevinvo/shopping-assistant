#!/usr/bin/env python3
import asyncio
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    print(
        "Error: websockets library not installed. Install with: pip install websockets"
    )
    sys.exit(1)


async def test_websocket_connection(websocket_url: str):
    """Make a simple WebSocket connection to trigger the connect handler."""
    try:
        print(f"Connecting to: {websocket_url}")
        async with websockets.connect(websocket_url) as _:
            print("✅ Connected successfully")
            await asyncio.sleep(1)
            print("✅ Connection test complete")
            return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False


def main():
    websocket_domain = "o5b5iy8371.execute-api.ap-southeast-1.amazonaws.com"
    websocket_stage = "chalice-test"
    websocket_url = f"wss://{websocket_domain}/{websocket_stage}/"

    print("=" * 70)
    print("Testing WebSocket Cold Start Measurement")
    print("=" * 70)
    print()

    print("Step 1: Making WebSocket connection...")
    success = asyncio.run(test_websocket_connection(websocket_url))

    if not success:
        print("\n❌ Failed to connect. Exiting.")
        sys.exit(1)

    print("\nStep 2: Waiting for logs to be available (5 seconds)...")
    time.sleep(5)

    print("\nStep 3: Analyzing cold start metrics...")
    print()

    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "analyze_cold_starts.py"),
            "--handler",
            "websocket_connect",
            "--stage",
            "chalice-test",
            "--hours",
            "1",
            "--output",
            "table",
        ],
        capture_output=False,
    )

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
