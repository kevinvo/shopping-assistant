from __future__ import annotations

from datetime import datetime

from chalicelib.models.data_objects import RedditPost


def create_daily_s3_key(subreddit_name: str, today: datetime | None = None) -> str:
    """Return the S3 key for the raw daily Reddit payload for a subreddit."""

    if today is None:
        today = datetime.now()

    return (
        f"created_at_year={today.year}/created_at_month={today.month}/"
        f"created_at_day={today.day}/subreddit_name={subreddit_name}/data.json"
    )


def create_complete_s3_key(subreddit_name: str) -> str:
    """Return the S3 object key that stores the subreddit top-post snapshot."""

    return f"top_posts/subreddit_name={subreddit_name}/data.json"


def create_parquet_post_s3_key(subreddit_name: str, post: RedditPost) -> str:
    """Return the S3 object key for a processed Reddit post parquet payload."""

    return (
        f"year={post.year}/month={post.month}/post_id={post.id}/"
        f"subreddit_name={subreddit_name}/data.snappy.parquet"
    )
