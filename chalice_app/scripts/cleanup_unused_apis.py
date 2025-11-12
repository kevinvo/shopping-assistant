#!/usr/bin/env python3
"""
Cleanup script for unused API Gateway REST APIs.

This script identifies and deletes REST APIs that are not referenced in
Chalice deployed state files, helping prevent hitting the 120 API limit
for EDGE endpoint type.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Optional
from datetime import datetime

import boto3
from botocore.exceptions import ClientError


def log(level: str, message: str) -> None:
    """Print log message with level prefix."""
    print(f"[{level}] {message}")


def load_deployed_apis(chalice_app_dir: Path) -> Set[str]:
    """
    Load all REST API IDs from Chalice deployed state files.

    Returns a set of API IDs that are currently in use.
    """
    deployed_dir = chalice_app_dir / ".chalice" / "deployed"
    if not deployed_dir.exists():
        log("WARN", f"Deployed directory not found: {deployed_dir}")
        return set()

    api_ids = set()

    for deployed_file in deployed_dir.glob("*.json"):
        try:
            with deployed_file.open("r", encoding="utf-8") as f:
                state = json.load(f)

            resources = state.get("resources", [])
            for resource in resources:
                if resource.get("resource_type") == "rest_api":
                    api_id = resource.get("rest_api_id")
                    if api_id:
                        api_ids.add(api_id)
                        log(
                            "INFO", f"Found active API {api_id} in {deployed_file.name}"
                        )

        except Exception as e:
            log("ERROR", f"Failed to read {deployed_file}: {e}")

    return api_ids


def list_all_rest_apis(region: str) -> List[Dict]:
    """List all REST APIs in the specified region (handles pagination)."""
    apigateway = boto3.client("apigateway", region_name=region)

    try:
        all_apis = []
        paginator = apigateway.get_paginator("get_rest_apis")

        for page in paginator.paginate():
            all_apis.extend(page.get("items", []))

        return all_apis
    except ClientError as e:
        log("ERROR", f"Failed to list REST APIs: {e}")
        sys.exit(1)


def delete_rest_api(
    api_id: str, api_name: str, region: str, dry_run: bool = True, max_retries: int = 3
) -> bool:
    """Delete a REST API with retry logic for rate limiting."""
    if dry_run:
        log("DRY-RUN", f"Would delete API {api_id} ({api_name})")
        return True

    apigateway = boto3.client("apigateway", region_name=region)
    import time

    for attempt in range(max_retries):
        try:
            apigateway.delete_rest_api(restApiId=api_id)
            log("INFO", f"✅ Deleted API {api_id} ({api_name})")
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NotFoundException":
                log("WARN", f"API {api_id} not found (may have been deleted already)")
                return True
            elif error_code == "TooManyRequestsException":
                if attempt < max_retries - 1:
                    wait_time = (2**attempt) * 2  # Exponential backoff: 2s, 4s, 8s
                    log(
                        "WARN",
                        f"Rate limited deleting {api_id}, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})",
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    log(
                        "ERROR",
                        f"Failed to delete API {api_id} after {max_retries} attempts: {e}",
                    )
                    return False
            else:
                log("ERROR", f"Failed to delete API {api_id}: {e}")
                return False

    return False


def format_date(date_str: Optional[str]) -> str:
    """Format ISO date string to readable format."""
    if not date_str:
        return "Unknown"
    try:
        # Handle both ISO format and timestamp format
        if isinstance(date_str, (int, float)):
            dt = datetime.fromtimestamp(
                date_str / 1000 if date_str > 1e10 else date_str
            )
        else:
            # Try ISO format first
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                # Try parsing as timestamp string
                dt = datetime.fromtimestamp(
                    float(date_str) / 1000
                    if float(date_str) > 1e10
                    else float(date_str)
                )
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(date_str)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Cleanup unused API Gateway REST APIs")
    parser.add_argument(
        "--chalice-app-dir",
        type=Path,
        default=Path(__file__).parent.parent,
        help="Path to chalice_app directory (default: parent of scripts/)",
    )
    parser.add_argument(
        "--region",
        default="ap-southeast-1",
        help="AWS region (default: ap-southeast-1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode - don't actually delete (default: True)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete APIs (overrides --dry-run)",
    )
    parser.add_argument(
        "--min-age-days",
        type=int,
        default=0,
        help="Minimum age in days before considering API for deletion (default: 0, set to 0 to delete all unused)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Delay between deletions to avoid rate limiting (default: 1.0)",
    )
    parser.add_argument(
        "--api-name-filter",
        default="shopping-assistant-api",
        help="Only delete APIs matching this name (default: shopping-assistant-api)",
    )
    parser.add_argument(
        "--countdown-seconds",
        type=int,
        default=2,
        help="Countdown before deletion starts (default: 2)",
    )

    args = parser.parse_args(argv)

    dry_run = not args.execute

    log("INFO", "=" * 70)
    log("INFO", "API Gateway REST API Cleanup Script")
    log("INFO", "=" * 70)
    log("INFO", f"Region: {args.region}")
    log("INFO", f"Chalice app dir: {args.chalice_app_dir}")
    log("INFO", f"Mode: {'DRY-RUN' if dry_run else 'EXECUTE'}")
    log("INFO", f"Minimum age: {args.min_age_days} days")
    log("INFO", f"API name filter: {args.api_name_filter}")
    log("INFO", "")

    # Load active APIs from deployed state
    log("INFO", "Loading active APIs from deployed state files...")
    active_api_ids = load_deployed_apis(args.chalice_app_dir)
    log(
        "INFO",
        f"Found {len(active_api_ids)} active API(s): {', '.join(sorted(active_api_ids))}",
    )
    log("INFO", "")

    # List all REST APIs
    log("INFO", "Listing all REST APIs in region...")
    all_apis = list_all_rest_apis(args.region)
    log("INFO", f"Found {len(all_apis)} total REST API(s)")
    log("INFO", "")

    # Identify unused APIs
    unused_apis = []
    cutoff_date = datetime.now().replace(tzinfo=None) - timedelta(
        days=args.min_age_days
    )

    for api in all_apis:
        api_id = api.get("id")
        api_name = api.get("name", "Unknown")
        created_date_str = api.get("createdDate")

        # Skip if API is in active list
        if api_id in active_api_ids:
            continue

        # Only delete APIs matching the filter name
        if args.api_name_filter and api_name != args.api_name_filter:
            log(
                "INFO",
                f"Skipping API {api_id} ({api_name}) - doesn't match filter '{args.api_name_filter}'",
            )
            continue

        # Check age
        if created_date_str:
            try:
                # Handle both ISO format and timestamp format
                if isinstance(created_date_str, (int, float)):
                    created_date = datetime.fromtimestamp(
                        created_date_str / 1000
                        if created_date_str > 1e10
                        else created_date_str
                    )
                else:
                    # Try ISO format first
                    try:
                        created_date = datetime.fromisoformat(
                            created_date_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        # Try parsing as timestamp string
                        created_date = datetime.fromtimestamp(
                            float(created_date_str) / 1000
                            if float(created_date_str) > 1e10
                            else float(created_date_str)
                        )

                created_date_naive = created_date.replace(tzinfo=None)
                if created_date_naive > cutoff_date:
                    log(
                        "INFO",
                        f"Skipping API {api_id} ({api_name}) - too new (created: {format_date(created_date_str)})",
                    )
                    continue
            except Exception as e:
                log(
                    "WARN",
                    f"Could not parse date for API {api_id}: {e} - will include in deletion list",
                )

        unused_apis.append(api)

    if not unused_apis:
        log("INFO", "✅ No unused APIs found to clean up!")
        return 0

    # Display unused APIs
    log("INFO", "=" * 70)
    log("INFO", f"Found {len(unused_apis)} unused API(s) to delete:")
    log("INFO", "=" * 70)

    for api in unused_apis:
        api_id = api.get("id")
        api_name = api.get("name", "Unknown")
        created_date = api.get("createdDate", "Unknown")
        endpoint_type = api.get("endpointConfiguration", {}).get("types", ["Unknown"])[
            0
        ]

        log("INFO", f"  - {api_id} ({api_name})")
        log("INFO", f"    Created: {format_date(created_date)}")
        log("INFO", f"    Endpoint Type: {endpoint_type}")

    log("INFO", "")

    if dry_run:
        log("INFO", "🔍 DRY-RUN mode: No APIs will be deleted")
        log("INFO", "   Run with --execute to actually delete these APIs")
        return 0

    # Confirm deletion
    log("WARN", "⚠️  EXECUTE mode: About to delete the APIs listed above")
    log("WARN", f"   Press Ctrl+C within {args.countdown_seconds} seconds to cancel...")

    try:
        import time

        for i in range(args.countdown_seconds, 0, -1):
            print(f"\r   Starting deletion in {i} seconds...", end="", flush=True)
            time.sleep(1)
        print()  # New line after countdown
    except KeyboardInterrupt:
        log("INFO", "\n❌ Deletion cancelled by user")
        return 1

    # Delete unused APIs
    log("INFO", "")
    log(
        "INFO",
        f"Deleting unused APIs (with {args.delay_seconds}s delay between deletions)...",
    )

    deleted_count = 0
    failed_count = 0

    import time

    for i, api in enumerate(unused_apis, 1):
        api_id = api.get("id")
        api_name = api.get("name", "Unknown")

        log("INFO", f"[{i}/{len(unused_apis)}] Processing {api_id} ({api_name})...")

        if delete_rest_api(api_id, api_name, args.region, dry_run=False):
            deleted_count += 1
        else:
            failed_count += 1

        # Add delay between deletions to avoid rate limiting (except for last one)
        if i < len(unused_apis):
            time.sleep(args.delay_seconds)

    log("INFO", "")
    log("INFO", "=" * 70)
    log("INFO", f"Cleanup complete: {deleted_count} deleted, {failed_count} failed")
    log("INFO", "=" * 70)

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    from datetime import timedelta

    sys.exit(main(sys.argv[1:]))
