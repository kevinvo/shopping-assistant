import os
import boto3
from typing import Dict, Any
from chalicelib.logger_config import setup_logger

logger = setup_logger(__name__)


def start_glue_job(glue_job_name: str = None) -> Dict[str, Any]:
    """
    Start a Glue job for processing Reddit data.

    Args:
        glue_job_name: Optional Glue job name (uses env var if not provided)

    Returns:
        Response with status code and job details
    """
    logger.info("Starting Glue job")

    # Get the Glue job name from parameter or environment variables
    if not glue_job_name:
        glue_job_name = os.environ.get("GLUE_JOB_NAME")
    if not glue_job_name:
        error_msg = "GLUE_JOB_NAME environment variable is not set"
        logger.error(error_msg)
        return {"statusCode": 500, "body": error_msg}

    logger.info(f"Starting Glue job: {glue_job_name}")

    # Initialize Glue client
    try:
        glue_client = boto3.client("glue")
        logger.info("Glue client initialized successfully")
    except Exception as e:
        error_msg = f"Failed to initialize Glue client: {str(e)}"
        logger.error(error_msg)
        return {"statusCode": 500, "body": error_msg}

    # Start the Glue job
    try:
        response = glue_client.start_job_run(JobName=glue_job_name)
        job_run_id = response.get("JobRunId")
        logger.info(f"Glue job started successfully. Job run ID: {job_run_id}")

        # Get job run details
        job_run = glue_client.get_job_run(JobName=glue_job_name, RunId=job_run_id)
        job_status = job_run.get("JobRun", {}).get("JobRunState", "UNKNOWN")
        logger.info(f"Glue job status: {job_status}")

        return {
            "statusCode": 200,
            "body": f"Glue job {glue_job_name} started successfully with run ID {job_run_id}",
            "jobRunId": job_run_id,
            "jobStatus": job_status,
        }
    except Exception as e:
        error_msg = f"Failed to start Glue job {glue_job_name}: {str(e)}"
        logger.error(error_msg)
        return {"statusCode": 500, "body": error_msg}
