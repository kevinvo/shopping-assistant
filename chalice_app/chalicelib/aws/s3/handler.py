import json
import boto3
from botocore.exceptions import ClientError
from chalicelib.models.data_objects import SubredditData
from chalicelib.core.logger_config import setup_logger
from chalicelib.core.config import config
from typing import List

logger = setup_logger(__name__)


class S3Handler:
    def __init__(self, bucket_name: str = config.s3_raw_reddit_bucket_name):
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=config.aws_credentials.aws_access_key_id,
            aws_secret_access_key=config.aws_credentials.aws_secret_access_key,
            region_name=config.aws_credentials.region_name,
        )
        self.bucket_name = bucket_name

    def get_reddit_posts(self, s3_key: str) -> SubredditData:
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
        data = json.loads(response["Body"].read().decode("utf-8"))
        return SubredditData(**data)

    def upload_file(self, s3_key: str, data: List[dict]) -> None:
        try:
            json_str = json.dumps(data)
            data_bytes = json_str.encode("utf-8")
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=data_bytes,
                ContentType="application/json",
            )
            logger.info(f"Successfully uploaded file to {s3_key}")
        except Exception as e:
            logger.error(f"Error uploading file to {s3_key}: {str(e)}")
            raise

    def upload_bytes(self, s3_key: str, data: bytes) -> None:
        try:
            self.s3_client.put_object(Bucket=self.bucket_name, Key=s3_key, Body=data)
            logger.info(f"Successfully uploaded bytes data to {s3_key}")
        except Exception as e:
            logger.error(f"Error uploading bytes data to {s3_key}: {str(e)}")
            raise

    def file_exists(self, s3_key: str) -> bool:
        """Check if file exists in S3 bucket."""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            error_code = int(e.response["Error"]["Code"])
            if error_code == 404:
                return False
            raise e

    def folder_exists(self, s3_prefix: str) -> bool:
        """Check if a folder exists in S3 bucket."""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=s3_prefix, Delimiter="/"
            )
            return "Contents" in response or "CommonPrefixes" in response
        except ClientError as e:
            logger.error(f"Error checking folder existence: {e}")
            return False
