from datetime import datetime, timedelta
from chalicelib.s3_key_utils import create_daily_s3_key, create_parquet_post_s3_key
from chalicelib.logger_config import setup_logger
from chalicelib.s3_handler import S3Handler
from chalicelib.lambda_constants import SubredditData, SUBREDDIT_NAMES, RedditPost
from chalicelib.config import config
import io
from typing import Dict, Any
import pandas as pd

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
                f"Skipping {subreddit_name} as it does not exist in S3. s3_key = {s3_key_pattern}"
            )
            return

        subreddit_data: SubredditData = self.raw_reddit_s3_handler.get_reddit_posts(
            s3_key=s3_key_pattern
        )
        for post in subreddit_data.posts:
            s3_key = create_parquet_post_s3_key(subreddit_name, post)
            if self.processed_reddit_s3_handler.file_exists(s3_key=s3_key):
                logger.info(
                    f"Skipping {post.id} as it already exists in S3. s3_key = {s3_key}"
                )
                continue
            self.processed_reddit_s3_handler.upload_bytes(
                s3_key=s3_key, data=self._convert_post_to_parquet(post=post)
            )
        logger.info(f"Processing {subreddit_data}")

    def process_daily_posts(self) -> None:
        for subreddit_name in SUBREDDIT_NAMES:
            for days_ago in range(7):
                day = datetime.now() - timedelta(days=days_ago)
                s3_key_pattern = create_daily_s3_key(
                    subreddit_name=subreddit_name, today=day
                )
                logger.info(
                    f"Processing {subreddit_name}. S3 key pattern: {s3_key_pattern}"
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
    # RedditPostIngestor().process_top_posts()
    RedditPostIngestor().process_daily_posts()
