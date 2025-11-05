import re
from typing import List, Dict
import praw
from datetime import datetime, timezone
import praw.models
import prawcore
from chalicelib.lambda_constants import (
    SUBREDDIT_NAMES,
    TimestampData,
    RedditComment,
    RedditPost,
    TimeFilter,
)
from chalicelib.logger_config import setup_logger
from chalicelib.post_tracker import PostTracker
from chalicelib.config import config
from chalicelib.s3_handler import S3Handler
from chalicelib.util import (
    measure_performance,
    create_daily_s3_key,
    create_complete_s3_key,
)
from collections import defaultdict

logger = setup_logger(__name__)

TOP_LIMIT = 200
DAILY_LIMIT = 50
COMMENTS_LIMIT = 5


class RedditScraper:
    # Class-level constants
    REMOVED_COMMENTS: List[str] = ["[removed]", "[deleted]"]
    REDDIT_FORMATTING_PATTERNS: Dict[str, tuple[str, str]] = {
        # "spoiler": (r"\>\!\s*(.*?)\s*\!\<", r"\1"),  # >!spoiler!<
        # "superscript": (r"\^(\S+|\([^)]+\))", r"\1"), # ^word or ^(words)
        # "table": (r"\|[^\n]*\|", " "),                # |table|cells|
        # "table_separator": (r"[-|:\s]+", " "),        # table formatting row
    }

    def __init__(self):
        self.reddit: praw.Reddit = praw.Reddit(
            client_id=config.reddit_credentials.client_id,
            client_secret=config.reddit_credentials.client_secret,
            user_agent=config.reddit_credentials.user_agent,
        )

    @staticmethod
    def _clean_reddit_text(text: str) -> str:
        """Clean and normalize Reddit text content."""
        if not text:
            return ""

        cleaned = text

        for pattern, replacement in RedditScraper.REDDIT_FORMATTING_PATTERNS.items():
            cleaned = re.sub(pattern[0], pattern[1], cleaned)

        # Remove extra whitespace
        cleaned = cleaned.strip()
        return re.sub(r"\s+", " ", cleaned)

    def _get_timestamp_data(self, timestamp: float) -> TimestampData:
        """Extract year and month from UTC timestamp."""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return TimestampData(year=dt.year, month=dt.month)

    def _process_comment(self, comment: praw.models.Comment) -> RedditComment:
        """Process a single comment."""
        timestamp_data = self._get_timestamp_data(timestamp=comment.created_utc)
        return RedditComment(
            id=comment.id,
            score=comment.score,
            body=comment.body,
            year=timestamp_data.year,
            month=timestamp_data.month,
        )

    def _process_post(
        self, post: praw.models.Submission, comments: List[RedditComment]
    ) -> RedditPost:
        """Process a single post."""
        timestamp_data = self._get_timestamp_data(timestamp=post.created_utc)
        return RedditPost(
            id=post.id,
            title=post.title,
            original_title=post.title,
            score=post.score,
            url=post.url,
            content=post.selftext,
            comments=comments,
            year=timestamp_data.year,
            month=timestamp_data.month,
            subreddit_name=post.subreddit.display_name,
        )

    @measure_performance
    def _batch_fetch_comments(
        self, posts: List[praw.models.Submission], comment_limit=COMMENTS_LIMIT
    ) -> Dict[str, List[RedditComment]]:
        comments_data: Dict[str, List[RedditComment]] = defaultdict(list)
        for post in posts:
            post.comments.replace_more(limit=0)  # Do not fetch "load more comments"
            top_comments = list(
                post.comments[:comment_limit]
            )  # Convert to list explicitly

            total_comments = len(top_comments)
            logger.info(
                f"Fetching comments for post ID: {post.id}, Total comments: {total_comments}"
            )
            for index, comment in enumerate(top_comments, start=1):
                if comment.body.lower() in self.REMOVED_COMMENTS:
                    continue
                comments_data[post.id].append(self._process_comment(comment=comment))

                # Calculate and log percentage progress
                progress_percentage = (index / total_comments) * 100
                logger.debug(
                    f"Processed comment ID: {comment.id} for post ID: {post.id} - {progress_percentage:.2f}% complete"
                )
        return comments_data

    def scrape_subreddit(
        self,
        subreddit_name: str,
        time_filter: TimeFilter = TimeFilter.ALL,
        limit: int = TOP_LIMIT,
        limit_comments: int = COMMENTS_LIMIT,
        max_retries: int = 3,
    ) -> List[RedditPost]:
        retry_count = 0
        while retry_count <= max_retries:
            try:
                subreddit = self.reddit.subreddit(display_name=subreddit_name)
                logger.info(f"Starting to fetch posts from r/{subreddit_name}")

                top_posts: List[praw.models.Submission] = list(
                    subreddit.top(limit=limit, time_filter=time_filter.value)
                )
                hot_posts = subreddit.hot(limit=limit) if limit == DAILY_LIMIT else []

                merged_posts = list(set(top_posts) | set(hot_posts))

                logger.info(f"Fetching comments for {len(top_posts)} posts")

                comments_data: Dict[str, List[RedditComment]] = (
                    self._batch_fetch_comments(
                        posts=merged_posts, comment_limit=limit_comments
                    )
                )
                reddit_posts: List[RedditPost] = [
                    self._process_post(
                        post=post, comments=comments_data.get(post.id, [])
                    )
                    for post in merged_posts
                ]
                return reddit_posts
            except prawcore.exceptions.TooManyRequests:
                if retry_count == max_retries:
                    logger.error(
                        f"Max retries ({max_retries}) exceeded for {subreddit_name}"
                    )
                    return []
                retry_count += 1
            except prawcore.exceptions.NotFound as e:
                logger.error(
                    f"Subreddit {subreddit_name} not foun. {str(e)}",
                    exc_info=True,
                )
                return []
            except Exception as e:
                logger.error(
                    f"Error scraping subreddit {subreddit_name}: {str(e)}",
                    exc_info=True,
                )
                return []
        return []  # Ensure function always returns a list


def scrap_daily_subreddits(
    subreddit_name: str = "",
    time_filter: TimeFilter = TimeFilter.DAY,
    limit: int = DAILY_LIMIT,
    limit_comments: int = COMMENTS_LIMIT,
):
    """Scrape daily top posts from a subreddit and store in S3."""
    s3_handler = S3Handler()
    s3_key = create_daily_s3_key(subreddit_name=subreddit_name, today=datetime.now())

    # Skip if already processed today
    if s3_handler.folder_exists(s3_prefix=s3_key):
        logger.info(
            f"Skipping {subreddit_name} as it already exists in S3. s3_key = {s3_key}"
        )
        return

    # Scrape subreddit data
    scraper = RedditScraper()
    post_tracker = PostTracker()

    reddit_posts: List[RedditPost] = scraper.scrape_subreddit(
        subreddit_name=subreddit_name,
        time_filter=time_filter,
        limit=limit,
        limit_comments=limit_comments,
    )
    if len(reddit_posts) == 0:
        logger.info(f"No new posts to save for {subreddit_name}")
        return

    # Filter out previously pulled posts
    post_pulled_status = post_tracker.batch_is_post_pulled(
        post_ids=[post.id for post in reddit_posts], subreddit=subreddit_name
    )
    posts_to_save = [
        post for post in reddit_posts if not post_pulled_status.get(post.id, False)
    ]

    if not posts_to_save:
        logger.info(f"No new posts to save for {subreddit_name}")
        return

    posts_json = [post.to_json() for post in posts_to_save]
    s3_key = create_daily_s3_key(subreddit_name=subreddit_name, today=datetime.now())
    s3_handler.upload_file(data=posts_json, s3_key=s3_key)

    post_tracker.batch_mark_posts_as_pulled(
        post_ids=[post.id for post in posts_to_save], subreddit=subreddit_name
    )


def scrap_complete_top_subreddits(
    subreddit_name: str = "",
    time_filter: TimeFilter = TimeFilter.ALL,
    limit: int = TOP_LIMIT,
    limit_comments: int = COMMENTS_LIMIT,
):
    """Scrape all-time top posts from a subreddit and store in S3."""
    s3_handler = S3Handler()
    s3_key = create_complete_s3_key(subreddit_name=subreddit_name)

    if s3_handler.folder_exists(s3_prefix=s3_key):
        logger.info(f"Skipping {subreddit_name} as it already exists in S3")
        return

    # Scrape subreddit data
    scraper = RedditScraper()
    reddit_posts: List[RedditPost] = scraper.scrape_subreddit(
        subreddit_name=subreddit_name,
        time_filter=time_filter,
        limit=limit,
        limit_comments=limit_comments,
    )
    logger.info(f"Subreddit {subreddit_name} subreddit_data fetched")

    if len(reddit_posts) == 0:
        logger.info(f"No new posts to save for {subreddit_name}")
        return

    logger.info(f"Subreddit {subreddit_name} writing to S3")
    posts_json = [post.to_json() for post in reddit_posts]
    logger.info(f"posts_json = {posts_json}")
    s3_handler.upload_file(data=posts_json, s3_key=s3_key)

    PostTracker().batch_mark_posts_as_pulled(
        post_ids=[post.id for post in reddit_posts],
        subreddit=subreddit_name,
    )


if __name__ == "__main__":
    for subreddit_name in SUBREDDIT_NAMES:
        scrap_daily_subreddits(subreddit_name=subreddit_name)
        scrap_complete_top_subreddits(subreddit_name=subreddit_name)
