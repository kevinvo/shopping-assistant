from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, cast

import boto3

from chalicelib.logger_config import setup_logger


logger = setup_logger(__name__)
s3_client = boto3.client("s3")


@dataclass(frozen=True)
class LayerCleanupConfig:
    bucket_name: str
    prefix: str = ""
    retention_days: int = 30
    min_versions_to_keep: int = 5


@dataclass(frozen=True)
class LayerCleanupResult:
    deleted: int
    protected: int
    total: int

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


def cleanup_old_layer_artifacts(config: LayerCleanupConfig) -> Dict[str, int]:
    """Delete Lambda layer artifacts that exceed the retention policy."""

    logger.info(
        "Starting layer artifact cleanup",
        extra={
            "bucket": config.bucket_name,
            "prefix": config.prefix,
            "retention_days": config.retention_days,
            "min_versions_to_keep": config.min_versions_to_keep,
        },
    )

    objects = list(_list_objects(bucket_name=config.bucket_name, prefix=config.prefix))

    if not objects:
        result = LayerCleanupResult(deleted=0, protected=0, total=0)
        logger.info(
            "No layer artifacts found; skipping cleanup",
            extra=result.to_dict(),
        )
        return result.to_dict()

    objects.sort(key=lambda obj: cast(datetime, obj["LastModified"]), reverse=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.retention_days)
    protected = objects[: max(config.min_versions_to_keep, 0)]
    protected_keys = {cast(str, obj["Key"]) for obj in protected}

    candidates = [
        obj
        for obj in objects[len(protected) :]
        if cast(datetime, obj["LastModified"]) < cutoff
    ]

    if not candidates:
        result = LayerCleanupResult(
            deleted=0, protected=len(protected_keys), total=len(objects)
        )
        logger.info(
            "No layer artifacts qualify for deletion after retention filter",
            extra=result.to_dict(),
        )
        return result.to_dict()

    deleted = _delete_objects(bucket_name=config.bucket_name, objects=candidates)

    result = LayerCleanupResult(
        deleted=deleted, protected=len(protected_keys), total=len(objects)
    )
    logger.info(
        "Layer artifact cleanup completed",
        extra=result.to_dict(),
    )

    return result.to_dict()


def _list_objects(*, bucket_name: str, prefix: str) -> Iterable[Dict[str, Any]]:
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix or None):
        for obj in page.get("Contents", []):
            yield obj


def _delete_objects(*, bucket_name: str, objects: List[Dict[str, Any]]) -> int:
    deleted = 0
    batch: List[Dict[str, str]] = []

    for obj in objects:
        batch.append({"Key": cast(str, obj["Key"])})
        if len(batch) == 1000:
            deleted += _execute_delete(bucket_name=bucket_name, delete_batch=batch)
            batch.clear()

    if batch:
        deleted += _execute_delete(bucket_name=bucket_name, delete_batch=batch)

    return deleted


def _execute_delete(*, bucket_name: str, delete_batch: List[Dict[str, str]]) -> int:
    response = s3_client.delete_objects(
        Bucket=bucket_name,
        Delete={"Objects": delete_batch, "Quiet": True},
    )

    return len(response.get("Deleted", []))
