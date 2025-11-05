import json
import boto3
from typing import Dict, Any
from chalicelib.lambda_constants import RedditPost
from chalicelib.logger_config import setup_logger
import time
from datetime import datetime

logger = setup_logger(__name__)

lambda_client = boto3.client("lambda")


def create_daily_s3_key(subreddit_name: str, today: datetime = datetime.now()) -> str:
    return f"created_at_year={today.year}/created_at_month={today.month}/created_at_day={today.day}/subreddit_name={subreddit_name}/data.json"


def create_complete_s3_key(subreddit_name: str) -> str:
    return f"top_posts/subreddit_name={subreddit_name}/data.json"


def create_parquet_post_s3_key(subreddit_name: str, post: RedditPost) -> str:
    """Create an S3 key for a specific Reddit post's parquet data."""
    return f"year={post.year}/month={post.month}/post_id={post.id}/subreddit_name={subreddit_name}/data.snappy.parquet"


def create_response(status_code: int, message: str) -> Dict[str, Any]:
    return {"statusCode": status_code, "body": json.dumps(message)}


def get_lambda_function_name() -> str:
    response = lambda_client.list_functions()
    for function in response["Functions"]:
        if function["Handler"] == "reddit_scraper_lambda.lambda_handler":
            return function["FunctionName"]
    raise ValueError(
        "Could not find Lambda function with handler 'reddit_scraper_lambda.lambda_handler'"
    )


performance_logger = setup_logger("performance_logger")


def measure_performance(func):
    """Decorator to measure the performance of a function."""

    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        performance_logger.info(
            f"Function '{func.__name__}' executed in {end_time - start_time:.4f} seconds"
        )
        return result

    return wrapper
