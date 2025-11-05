import os
from enum import Enum
from typing import Dict
import boto3
import json

from dotenv import load_dotenv
from dataclasses import dataclass

from chalicelib.core.logger_config import setup_logger

logger = setup_logger(__name__)
load_dotenv()

RAW_REDDIT_TEST_DATA_BUCKET_NAME = "shopping-assistant-raw-test-reddit-data"
RAW_REDDIT_DATA_BUCKET_NAME = "shopping-assistant-raw-reddit-data"

REDDIT_POSTS_TABLE_NAME = "reddit-posts"
REDDIT_POSTS_TEST_TABLE_NAME = "reddit-posts-test"
PROCESSED_REDDIT_DATA_BUCKET_NAME = "shopping-assistant-processed-reddit-data"
PROCESSED_REDDIT_TEST_DATA_BUCKET_NAME = "shopping-assistant-processed-test-reddit-data"


class Environment(Enum):
    DEV = "dev"
    # PROD = "prod"
    PROD = "chalice-test"


@dataclass
class WeaviateConfig:
    weaviate_url: str
    weaviate_api_key: str


@dataclass
class RedditCredentials:
    client_id: str
    client_secret: str
    user_agent: str

    def to_s(self) -> str:
        client_secret_length = len(self.client_secret)
        log_message = (
            f"Reddit Credentials - Client ID: {self.client_id}, "
            f"User Agent: {self.user_agent}, Client Secret Length: {client_secret_length}"
        )
        return log_message


@dataclass
class AWSCredentials:
    aws_access_key_id: str
    aws_secret_access_key: str
    region_name: str

    def to_s(self) -> str:
        secret_key_length = len(self.aws_secret_access_key)
        log_message = (
            f"AWS Credentials - Access Key ID: {self.aws_access_key_id}, "
            f"Region: {self.region_name}, Secret Key Length: {secret_key_length}"
        )
        return log_message


class AppConfig:
    def __init__(self, env: Environment | None = None):
        self.env = self._resolve_env(env)
        self._credentials = self._load_credentials()

    @staticmethod
    def _resolve_env(env: Environment | None) -> Environment:
        if env is not None:
            return env

        raw_env = os.environ.get("ENVIRONMENT", Environment.DEV.value).lower()
        alias_map = {
            "prod": Environment.PROD,
            "production": Environment.PROD,
        }

        if raw_env in alias_map:
            return alias_map[raw_env]

        try:
            return Environment(raw_env)
        except ValueError:
            return Environment.DEV

    def _load_credentials(self) -> Dict:
        logger.info(f"Initializing AppConfig with environment: {self.env.value}")
        secret_name = f"{self.env.value}/shopping-assistant/app"
        logger.info(f"Loading credentials from Secrets Manager for {secret_name}")

        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            secret_value = client.get_secret_value(SecretId=secret_name)
            credentials = json.loads(secret_value["SecretString"])
            return credentials.get("REDDIT", {})
        except Exception as e:
            logger.error(f"Error loading credentials from Secrets Manager: {e}")
            return {}

    def _require_credential(self, key: str) -> str:
        value = self._credentials.get(key)
        if not isinstance(value, str) or value == "":
            raise ValueError(f"{key} not found in credentials")
        return value

    @property
    def openai_api_key(self) -> str:
        api_key = self._credentials.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not found in credentials")
        return api_key

    @property
    def deepseek_api_key(self) -> str:
        api_key = self._credentials.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK API key not found in credentials")
        return api_key

    @property
    def anthropic_api_key(self) -> str:
        api_key = self._credentials.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Anthropic API key not found in credentials")
        return api_key

    @property
    def qdrant_api_key(self) -> str:
        api_key = self._credentials.get("QDRANT_API_KEY")
        if not api_key:
            raise ValueError("QDRANT API key not found in credentials")
        return api_key

    @property
    def qdrant_url(self) -> str:
        url = self._credentials.get("QDRANT_URL")
        if not url:
            raise ValueError("QDRANT URL not found in credentials")
        return url

    @property
    def weaviate_config(self) -> WeaviateConfig:
        return WeaviateConfig(
            weaviate_url=self._require_credential("WEAVIATE_URL"),
            weaviate_api_key=self._require_credential("WEAVIATE_API_KEY"),
        )

    @property
    def aws_credentials(self) -> AWSCredentials:
        aws_creds = AWSCredentials(
            aws_access_key_id=self._credentials.get("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=self._credentials.get("AWS_SECRET_ACCESS_KEY", ""),
            region_name=self._credentials.get("AWS_REGION", ""),
        )
        return aws_creds

    @property
    def reddit_credentials(self) -> RedditCredentials:
        return RedditCredentials(
            client_id=self._credentials.get("CLIENT_ID", ""),
            client_secret=self._credentials.get("CLIENT_SECRET", ""),
            user_agent=self._credentials.get("USER_AGENT", ""),
        )

    def to_s(self) -> str:
        aws_creds_str = self.aws_credentials.to_s()
        reddit_creds_str = self.reddit_credentials.to_s()
        log_message = (
            f"AppConfig - Environment: {self.env.value}, \n"
            f"AWS Credentials: {aws_creds_str}, \n"
            f"Reddit Credentials: {reddit_creds_str}, \n"
            f"OpenAI API Key Length: {len(self.openai_api_key)} \n"
            f"DeepSeek API Key Length: {len(self.deepseek_api_key)} \n"
            f"Anthropic API Key Length: {len(self.anthropic_api_key) if hasattr(self, 'anthropic_api_key') else 0} \n"
        )
        return log_message

    @property
    def dynamodb_table_name(self) -> str:
        return {
            Environment.PROD: REDDIT_POSTS_TABLE_NAME,
            Environment.DEV: REDDIT_POSTS_TEST_TABLE_NAME,
        }[self.env]

    @property
    def s3_raw_reddit_bucket_name(self) -> str:
        return {
            Environment.PROD: RAW_REDDIT_DATA_BUCKET_NAME,
            Environment.DEV: RAW_REDDIT_TEST_DATA_BUCKET_NAME,
        }[self.env]

    @property
    def processed_reddit_data_bucket_name(self) -> str:
        return {
            Environment.PROD: PROCESSED_REDDIT_DATA_BUCKET_NAME,
            Environment.DEV: PROCESSED_REDDIT_TEST_DATA_BUCKET_NAME,
        }[self.env]

    @property
    def langsmith_api_key(self) -> str:
        return self._credentials.get("LANGSMITH_API_KEY", "")

    @property
    def langsmith_api_url(self) -> str:
        return self._credentials.get(
            "LANGSMITH_API_URL", "https://api.smith.langchain.com"
        )


# Create a singleton instance
config = AppConfig()
logger.info(f"config = {config.to_s()}")
