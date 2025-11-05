"""Utilities for ingesting Reddit posts from raw S3 dumps into processed storage."""

from datetime import datetime, timedelta
import io
from typing import Any, Dict

import pandas as pd

from chalicelib.core.logger_config import setup_logger
from chalicelib.aws.s3.handler import S3Handler
from chalicelib.aws.s3.keys import create_daily_s3_key, create_parquet_post_s3_key
from chalicelib.models.lambda_constants import (
    SubredditData,
    SUBREDDIT_NAMES,
    RedditPost,
)
from chalicelib.core.config import config


logger = setup_logger(__name__)


class RedditPostIngestor:
    def __init__(self):
        self.raw_reddit_s3_handler = S3Handler(
            bucket_name=config.s3_raw_reddit_bucket_name
        )
        self.processed_reddit_s3_handler = S3Handler(
            bucket_name=config.processed_reddit_data_bucket_name
        )

    def _process_subreddit_posts(
        self, subreddit_name: str, s3_key_pattern: str
    ) -> None:
        """Process posts for a given subreddit and S3 key pattern."""

        if not self.raw_reddit_s3_handler.file_exists(s3_key=s3_key_pattern):
            logger.info(
                "Skipping %s as it does not exist in S3. s3_key = %s",
                subreddit_name,
                s3_key_pattern,
            )
            return

        subreddit_data: SubredditData = self.raw_reddit_s3_handler.get_reddit_posts(
            s3_key=s3_key_pattern
        )
        for post in subreddit_data.posts:
            s3_key = create_parquet_post_s3_key(subreddit_name, post)
            if self.processed_reddit_s3_handler.file_exists(s3_key=s3_key):
                logger.info(
                    "Skipping %s as it already exists in S3. s3_key = %s",
                    post.id,
                    s3_key,
                )
                continue
            self.processed_reddit_s3_handler.upload_bytes(
                s3_key=s3_key, data=self._convert_post_to_parquet(post=post)
            )
        logger.info("Processing %s", subreddit_data)

    def process_daily_posts(self) -> None:
        for subreddit_name in SUBREDDIT_NAMES:
            for days_ago in range(7):
                day = datetime.now() - timedelta(days=days_ago)
                s3_key_pattern = create_daily_s3_key(
                    subreddit_name=subreddit_name, today=day
                )
                logger.info(
                    "Processing %s. S3 key pattern: %s",
                    subreddit_name,
                    s3_key_pattern,
                )
                self._process_subreddit_posts(
                    subreddit_name=subreddit_name, s3_key_pattern=s3_key_pattern
                )

    def process_top_posts(self) -> None:
        for subreddit_name in SUBREDDIT_NAMES:
            s3_key_pattern = f"top_posts/{subreddit_name}/data.json"
            self._process_subreddit_posts(
                subreddit_name=subreddit_name, s3_key_pattern=s3_key_pattern
            )

    def _convert_post_to_parquet(self, post: RedditPost) -> bytes:
        post_dict: Dict[str, Any] = post.to_json()
        df = pd.DataFrame([post_dict])
        parquet_buffer: io.BytesIO = io.BytesIO()
        df.to_parquet(parquet_buffer, compression="snappy", index=False)
        parquet_buffer.seek(0)

        return parquet_buffer.getvalue()


if __name__ == "__main__":
    RedditPostIngestor().process_daily_posts()
