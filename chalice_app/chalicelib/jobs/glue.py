"""Glue job starter utility."""

import os
from typing import Any, Dict

import boto3

from chalicelib.core.logger_config import setup_logger


logger = setup_logger(__name__)


def start_glue_job(glue_job_name: str | None = None) -> Dict[str, Any]:
    """Start a Glue job for processing Reddit data."""

    logger.info("Starting Glue job")

    if not glue_job_name:
        glue_job_name = os.environ.get("GLUE_JOB_NAME")
    if not glue_job_name:
        error_msg = "GLUE_JOB_NAME environment variable is not set"
        logger.error(error_msg)
        return {"statusCode": 500, "body": error_msg}

    logger.info("Starting Glue job: %s", glue_job_name)

    try:
        glue_client = boto3.client("glue")
        logger.info("Glue client initialized successfully")
    except Exception as exc:  # pragma: no cover - defensive logging
        error_msg = f"Failed to initialize Glue client: {exc}"
        logger.error(error_msg)
        return {"statusCode": 500, "body": error_msg}

    try:
        response = glue_client.start_job_run(JobName=glue_job_name)
        job_run_id = response.get("JobRunId")
        logger.info("Glue job started successfully. Job run ID: %s", job_run_id)

        job_run = glue_client.get_job_run(JobName=glue_job_name, RunId=job_run_id)
        job_status = job_run.get("JobRun", {}).get("JobRunState", "UNKNOWN")
        logger.info("Glue job status: %s", job_status)

        return {
            "statusCode": 200,
            "body": (
                f"Glue job {glue_job_name} started successfully with run ID {job_run_id}"
            ),
            "jobRunId": job_run_id,
            "jobStatus": job_status,
        }
    except Exception as exc:  # pragma: no cover - defensive logging
        error_msg = f"Failed to start Glue job {glue_job_name}: {exc}"
        logger.error(error_msg)
        return {"statusCode": 500, "body": error_msg}
