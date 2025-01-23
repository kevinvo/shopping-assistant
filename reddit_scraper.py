import math
import os
import re
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import praw
from cleantext import clean
import json
from unidecode import unidecode
import datetime
from dataclasses import dataclass, asdict
from enum import Enum
import boto3
from botocore.exceptions import ClientError

load_dotenv()


@dataclass
class RedditComment:
    id: str
    score: int
    body: str
    created_utc: float
    year: int
    month: int


@dataclass
class RedditPost:
    id: str
    title: str
    original_title: str
    score: int
    url: str
    content: str
    original_content: str
    comments: List[RedditComment]
    created_utc: float
    year: int
    month: int


@dataclass
class SubredditData:
    subreddit: str
    post_count: int
    posts: List[RedditPost]


@dataclass
class TimestampData:
    created_utc: float
    year: int
    month: int


class TimeFilter(Enum):
    ALL = "all"
    DAY = "day"


def _write_to_s3(
    data: SubredditData, 
    bucket_name: str,
    file_name: str,
) -> None:
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
    )

    json_data = json.dumps(asdict(data), indent=2)
    s3_key = f"top_posts/{data.subreddit.lower()}/{file_name}"

    try:
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json_data,
            ContentType="application/json",
        )
        print(
            f"Successfully uploaded data for r/{data.subreddit} to s3://{bucket_name}/{s3_key}"
        )
    except ClientError as e:
        print(f"Error uploading to S3: {e}")
        # Still print the data to console as fallback
        print(json_data)


# Constants
SUBREDDIT_NAMES: List[str] = [
    "BuyItForLife",
    "Gadgets",
    "Frugal",
    "SuggestALaptop",
    "whatisthisthing",
    "Deals",
    "buildapcsales",
    "GameDeals",
    "PersonalFinance",
    "shutupandtakemymoney",
    "Cooking",
    "Beauty",
    "HomeImprovement",
    "productivity",
]

class RedditScraper:
    # Class-level constants
    REMOVED_COMMENTS = ["[removed]", "[deleted]"]
    REDDIT_FORMATTING_PATTERNS = {
        # "spoiler": (r"\>\!\s*(.*?)\s*\!\<", r"\1"),  # >!spoiler!<
        # "superscript": (r"\^(\S+|\([^)]+\))", r"\1"), # ^word or ^(words)
        # "table": (r"\|[^\n]*\|", " "),                # |table|cells|
        # "table_separator": (r"[-|:\s]+", " "),        # table formatting row
    }

    def __init__(self):
        self.reddit = praw.Reddit(
            client_id=os.getenv("REDDIT_CLIENT_ID"),
            client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
            user_agent=os.getenv("REDDIT_USER_AGENT"),
        )

    @staticmethod
    def _clean_reddit_text(text: str) -> str:
        """Clean and normalize Reddit text content."""
        if not text:
            return ""

        # Make a copy of the text to avoid modifying the original
        cleaned = text

        # Only remove specific Reddit formatting
        for pattern, replacement in RedditScraper.REDDIT_FORMATTING_PATTERNS.items():
            cleaned = re.sub(pattern[0], pattern[1], cleaned)

        # Remove extra whitespace
        cleaned = cleaned.strip()
        cleaned = re.sub(r"\s+", " ", cleaned)

        return cleaned

    def _get_timestamp_data(self, timestamp: float) -> TimestampData:
        """Extract year and month from UTC timestamp."""
        dt = datetime.datetime.fromtimestamp(timestamp)
        return TimestampData(
            created_utc=timestamp,
            year=int(dt.strftime("%Y")),
            month=int(dt.strftime("%m")),
        )

    def _process_comment(self, comment: praw.models.Comment) -> RedditComment:
        """Process a single comment."""
        timestamp_data = self._get_timestamp_data(comment.created_utc)
        return RedditComment(
            id=comment.id,
            score=comment.score,
            body=self._clean_reddit_text(comment.body),
            created_utc=timestamp_data.created_utc,
            year=timestamp_data.year,
            month=timestamp_data.month,
        )

    def _process_post(
        self, post: praw.models.Submission, limit_comments: int = 10
    ) -> RedditPost:
        """Process a single post and its comments."""
        # Clean post content
        cleaned_title = self._clean_reddit_text(post.title)
        cleaned_content = self._clean_reddit_text(post.selftext)

        # Get comments
        post.comments.replace_more(limit=0)
        comments_data = [
            self._process_comment(comment)
            for comment in post.comments[:limit_comments]
            if not comment.body.lower() in self.REMOVED_COMMENTS
        ]

        # Build post data
        timestamp_data = self._get_timestamp_data(post.created_utc)
        return RedditPost(
            id=post.id,
            title=cleaned_title,
            original_title=post.title,
            score=post.score,
            url=post.url,
            content=cleaned_content,
            original_content=post.selftext if post.selftext else "",
            comments=comments_data,
            created_utc=timestamp_data.created_utc,
            year=timestamp_data.year,
            month=timestamp_data.month,
        )

    def scrape_subreddit(
        self,
        subreddit_name: str,
        time_filter: TimeFilter = TimeFilter.ALL,
        limit: int = 10,
        limit_comments: int = 10,
    ) -> SubredditData:
        subreddit = self.reddit.subreddit(display_name=subreddit_name)

        posts_data: List[RedditPost] = [
            self._process_post(post=post, limit_comments=limit_comments)
            for post in subreddit.top(limit=limit, time_filter=time_filter.value)
        ]

        return SubredditData(
            subreddit=subreddit_name, post_count=len(posts_data), posts=posts_data
        )


def scrap_all_top_subreddits():
    scraper = RedditScraper()
    for subreddit_name in SUBREDDIT_NAMES:
        subreddit_name = subreddit_name.lower()
        subreddit_data = scraper.scrape_subreddit(
            subreddit_name=subreddit_name,
            time_filter=TimeFilter.ALL,
            limit=2,
            limit_comments=10,
        )
        _write_to_s3(data=subreddit_data, 
                     bucket_name="raw-reddit-data-shopping-assistant", 
                     file_name=f"{subreddit_name}.json")

if __name__ == "__main__":
    scrap_all_top_subreddits()
