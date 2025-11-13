#!/usr/bin/env python3
"""Publish the Chalice Lambda layer using boto3."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import subprocess
import platform
import zipfile

import boto3
from botocore.exceptions import ClientError

DEFAULT_LAYER_NAME = "shopping-assistant-chalice-dependencies"
DEFAULT_REGION = "ap-southeast-1"
MAX_DIRECT_UPLOAD = 50 * 1024 * 1024  # 50 MB


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


class PublishError(RuntimeError):
    """Raised when publishing the layer fails."""


def ensure_layer_contents(layer_dir: Path) -> None:
    python_dir = layer_dir / "python"
    if not python_dir.exists() or not any(python_dir.rglob("*")):
        raise PublishError(
            "Layer contents missing. Run 'bash scripts/build-layer.sh' before publishing."
        )


def prune_layer_contents(layer_dir: Path) -> None:
    python_root = layer_dir / "python"
    if not python_root.exists():
        raise PublishError(f"Expected python directory at {python_root}")

    pruning_targets: List[Tuple[str, str, bool]] = [
        (
            "torch",
            "PyTorch not required for SemanticChunker when using external embeddings.",
            False,
        ),
        ("torch-*.dist-info", "Remove PyTorch metadata.", False),
        (
            "torchvision",
            "Torchvision pulled in by torch wheel but unused in Lambda layer.",
            False,
        ),
        ("torchvision-*.dist-info", "Remove Torchvision metadata.", False),
        (
            "transformers",
            "SemanticChunker does not rely on HuggingFace transformers in this stack.",
            False,
        ),
        ("transformers-*.dist-info", "Remove transformers metadata.", False),
        (
            "sentence_transformers",
            "Relying on remote embeddings, so sentence_transformers can be dropped.",
            False,
        ),
        (
            "sentence_transformers-*.dist-info",
            "Remove sentence-transformers metadata.",
            False,
        ),
        (
            "diffusers",
            "Large diffusion library is unused.",
            False,
        ),
        ("diffusers-*.dist-info", "Remove diffusers metadata.", False),
        (
            "numpy/random/_examples",
            "Example assets not required at runtime.",
            False,
        ),
        ("langchain*/**/tests", "Strip LangChain test suites.", True),
        ("langchain*/**/__pycache__", "Prune pycache directories.", True),
        (
            "grpc_tools",
            "gRPC tooling is only needed for code generation, not Lambda runtime.",
            False,
        ),
        ("grpc_tools-*.dist-info", "Remove grpc_tools metadata.", False),
        (
            "zstandard",
            "Zstandard compression bindings are unused and add ~20MB.",
            False,
        ),
        ("zstandard-*.dist-info", "Remove zstandard metadata.", False),
        (
            "pandas/tests",
            "Drop pandas test data to save space.",
            True,
        ),
        ("pandas", "Remove pandas from Lambda layer; not required at runtime.", False),
        ("pandas-*.dist-info", "Remove pandas metadata.", False),
        ("numpy/tests", "Drop NumPy test suites.", True),
        ("numpy/**/tests", "Drop nested NumPy tests.", True),
        ("numpy/**/__pycache__", "Drop NumPy pycache directories.", True),
        ("grpc", "gRPC runtime not used by Chalice Lambdas.", False),
        ("grpc-*.dist-info", "Remove gRPC metadata.", False),
    ]

    removed_items: list[str] = []

    def _remove_paths(paths: Iterable[Path]) -> None:
        nonlocal removed_items
        for item in paths:
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                removed_items.append(str(item.relative_to(python_root)))
            except Exception as exc:
                log("WARN", f"Failed pruning {item}: {exc}")

    for pattern, reason, recursive in pruning_targets:
        if recursive:
            matched = list(python_root.rglob(pattern))
        else:
            matched = list(python_root.glob(pattern))
        if matched:
            log("INFO", f"Pruning {pattern}: {reason}")
            _remove_paths(matched)

    if removed_items:
        log("INFO", f"Pruned {len(removed_items)} items from layer.")
    else:
        log("INFO", "No optional dependencies were pruned.")

    _prune_numpy_shared_libs(python_root)
    _strip_shared_objects(python_root)


def _prune_numpy_shared_libs(python_root: Path) -> None:
    numpy_libs_dir = python_root / "numpy.libs"
    if not numpy_libs_dir.exists():
        return

    keep_prefixes = ("libopenblas", "libgfortran", "libquadmath")
    removed = []

    for shared_lib in numpy_libs_dir.iterdir():
        if not shared_lib.is_file():
            continue
        if not shared_lib.name.startswith(keep_prefixes):
            try:
                shared_lib.unlink(missing_ok=True)
                removed.append(shared_lib.name)
            except Exception as exc:
                log("WARN", f"Failed removing {shared_lib}: {exc}")

    if removed:
        log("INFO", f"Removed {len(removed)} unused NumPy shared libs: {removed}")

    if platform.system() != "Linux":
        log("INFO", "Skipping NumPy shared library stripping on non-Linux platform.")
        return

    strip_path = shutil.which("strip")
    if strip_path is None:
        log(
            "INFO",
            "strip tool not available; skipping shared library symbol stripping.",
        )
        return

    strip_args = (
        [strip_path, "--strip-unneeded"]
        if platform.system() != "Darwin"
        else [strip_path, "-S"]
    )

    for shared_lib in numpy_libs_dir.glob("*.so*"):
        try:
            subprocess.run(
                strip_args + [str(shared_lib)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log("WARN", f"Failed stripping {shared_lib}: {exc}")


def _strip_shared_objects(python_root: Path) -> None:
    if platform.system() != "Linux":
        log("INFO", "Skipping shared library stripping on non-Linux platform.")
        return

    strip_path = shutil.which("strip")
    if strip_path is None:
        log(
            "INFO",
            "strip tool not available; skipping global shared library stripping.",
        )
        return

    strip_args = (
        [strip_path, "--strip-unneeded"]
        if platform.system() != "Darwin"
        else [strip_path, "-S"]
    )

    for shared_lib in python_root.rglob("*.so"):
        if not shared_lib.is_file():
            continue
        try:
            subprocess.run(
                strip_args + [str(shared_lib)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log("WARN", f"Failed stripping {shared_lib}: {exc}")


def create_layer_zip(layer_dir: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp())
    zip_path = temp_dir / "layer.zip"
    python_root = layer_dir / "python"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in python_root.rglob("*"):
            if path.is_file():
                arcname = Path("python") / path.relative_to(python_root)
                zf.write(path, arcname)
    return zip_path


def ensure_bucket_exists(s3_client, bucket: str, region: str) -> None:
    try:
        s3_client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code not in ("404", "NoSuchBucket", "403"):
            raise PublishError(
                f"Failed checking bucket {bucket}: {exc.response.get('Error', {}).get('Message', exc)}"
            ) from exc
        log("INFO", f"Creating S3 bucket {bucket}")
        params: Dict[str, Any] = {"Bucket": bucket}
        if region != "us-east-1":
            params["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3_client.create_bucket(**params)


def upload_layer_to_s3(zip_path: Path, bucket: str, region: str) -> str:
    s3_client = boto3.client("s3", region_name=region)
    ensure_bucket_exists(s3_client, bucket, region)
    key = (
        f"lambda-layers/{zip_path.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
    )
    log("INFO", f"Uploading layer to s3://{bucket}/{key}")
    s3_client.upload_file(str(zip_path), bucket, key)
    return key


def publish_layer(
    *,
    layer_dir: Path,
    layer_name: str,
    region: str,
    bucket: Optional[str] = None,
) -> str:
    ensure_layer_contents(layer_dir)
    prune_layer_contents(layer_dir)
    zip_path = create_layer_zip(layer_dir)
    zip_size = zip_path.stat().st_size
    log("INFO", f"Layer zip path: {zip_path} ({zip_size} bytes)")

    lambda_client = boto3.client("lambda", region_name=region)

    try:
        if zip_size > MAX_DIRECT_UPLOAD:
            bucket = bucket or f"shopping-assistant-layers-{region}"
            key = upload_layer_to_s3(zip_path, bucket, region)
            log("INFO", "Publishing layer from S3")
            response = lambda_client.publish_layer_version(
                LayerName=layer_name,
                Description="Dependencies for shopping-assistant Chalice app",
                Content={"S3Bucket": bucket, "S3Key": key},
                CompatibleRuntimes=["python3.12"],
            )
        else:
            log("INFO", "Publishing layer via direct upload")
            response = lambda_client.publish_layer_version(
                LayerName=layer_name,
                Description="Dependencies for shopping-assistant Chalice app",
                ZipFile=zip_path.read_bytes(),
                CompatibleRuntimes=["python3.12"],
            )
    except ClientError as exc:
        raise PublishError(
            f"Failed to publish layer: {exc.response.get('Error', {}).get('Message', exc)}"
        ) from exc
    finally:
        shutil.rmtree(zip_path.parent, ignore_errors=True)

    return response.get("LayerVersionArn", "")


def save_layer_arn(app_dir: Path, layer_arn: str) -> None:
    chalice_dir = app_dir / ".chalice"
    chalice_dir.mkdir(exist_ok=True)
    target = chalice_dir / "layer-arn.txt"
    target.write_text(layer_arn + "\n", encoding="utf-8")
    log("INFO", f"Layer ARN saved to {target}")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Publish Chalice Lambda layer")
    parser.add_argument("layer_name", nargs="?", default=DEFAULT_LAYER_NAME)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--s3-bucket",
        default=None,
        help="Optional S3 bucket for large layer uploads",
    )
    return parser.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    scripts_dir = Path(__file__).resolve().parent
    app_dir = scripts_dir.parent
    layer_dir = app_dir / "layer"

    try:
        layer_arn = publish_layer(
            layer_dir=layer_dir,
            layer_name=args.layer_name,
            region=args.region,
            bucket=args.s3_bucket,
        )
    except PublishError as exc:
        log("ERROR", str(exc))
        return 1

    if not layer_arn:
        log("ERROR", "Layer published but ARN not returned")
        return 1

    log("INFO", "✅ Layer published successfully")
    log("INFO", f"Layer ARN: {layer_arn}")
    save_layer_arn(app_dir, layer_arn)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
