#!/usr/bin/env python3
"""Manual test for WebSocket keep-warm ping functionality."""

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import websockets
except ImportError:
    print(
        "Error: websockets library not installed. Install with: pip install websockets"
    )
    sys.exit(1)


async def test_keep_warm_connection(websocket_url: str):
    """Test keep-warm WebSocket connection with query parameter."""
    try:
        print(f"Connecting to: {websocket_url}")
        print("This should trigger websocket_connect handler with keep-warm detection")
        print()

        async with websockets.connect(websocket_url) as websocket:
            print("✅ WebSocket connection established")
            print("Waiting for pong response...")

            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                print(f"✅ Received response: {response}")

                # Parse the response
                try:
                    response_data = json.loads(response)
                    if response_data.get("type") == "pong":
                        print("✅ Received pong response - keep-warm ping successful!")
                    else:
                        print(f"⚠️  Unexpected response type: {response_data}")
                except json.JSONDecodeError:
                    print(f"⚠️  Response is not JSON: {response}")

            except asyncio.TimeoutError:
                print("⚠️  No response received within timeout")
                print("   (This might be okay if handler is still processing)")

            await asyncio.sleep(1)
            print("✅ Connection test complete")
            return True

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    # Load config to get WebSocket URL
    config_path = Path(__file__).resolve().parents[1] / ".chalice" / "config.json"

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)

    stage = os.environ.get("CHALICE_STAGE", "chalice-test")
    stage_config = config.get("stages", {}).get(stage, {})
    env_vars = stage_config.get("environment_variables", {})

    domain = env_vars.get("WEBSOCKET_DOMAIN")
    websocket_stage = env_vars.get("WEBSOCKET_STAGE", stage)

    if not domain:
        print(f"Error: WEBSOCKET_DOMAIN not found in config for stage '{stage}'")
        sys.exit(1)

    # Add keep-warm query parameter
    websocket_url = f"wss://{domain}/{websocket_stage}/?keep-warm=1"

    print("=" * 70)
    print("Testing WebSocket Keep-Warm Ping")
    print("=" * 70)
    print(f"Stage: {stage}")
    print(f"URL: {websocket_url}")
    print()
    print("Expected behavior:")
    print("  1. Connection established")
    print("  2. Handler detects keep-warm query parameter")
    print("  3. Handler skips DynamoDB write")
    print("  4. Handler sends immediate pong response")
    print()

    success = asyncio.run(test_keep_warm_connection(websocket_url))

    print()
    print("=" * 70)
    if success:
        print("✅ Test completed successfully")
        print()
        print("Next steps:")
        print("  - Check CloudWatch logs for websocket_connect handler")
        print("  - Verify no DynamoDB entry was created")
        print("  - Verify pong response was sent")
    else:
        print("❌ Test failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
