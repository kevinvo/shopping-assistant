from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime
from chalicelib.logger_config import setup_logger

logger = setup_logger(__name__)


SUBREDDIT_NAMES: List[str] = [
    "buyitforlife",
    "gadgets",
    "frugal",
    "suggestalaptop",
    "whatisthisthing",
    "deals",
    "buildapcsales",
    "gamedeals",
    "personalfinance",
    "shutupandtakemymoney",
    "cooking",
    "beauty",
    "homeimprovement",
    "productivity",
    "backpacks",
    "malefashionadvice",
    "frugalmalefashion",
    "femalefashionadvice",
    "goodvalue",
    "thriftstorehauls",
    "homeautomation",
    "smarthome",
    "edc",  # Everyday Carry
    "coffee",
    "headphones",
    "gaming",
    "gamingdeals",
    "shutupandtakemymoney",
    "frugalliving",
    "zerowaste",
    "minimalism",
    "techsupport",
    "android",
    "ios",
    "apple",
    "diy",
    "interiordesign",
    "mealprepsunday",
    "travelhacks",
    "askhistorians",
    "skincareaddiction",
    "rawdenim",
    "askreddit",
    "askmenover30",
    "askwomenover30",
    "askmen",
    "askwomen",
    "askreddit",
    "jacquesmariemage",
    "sunglasses",
    "GiftIdeas",
    "perfectgift",
    "HelpMeFind",
]


# Type alias for JSON-like dictionary
JsonDict = Dict[str, Any]


@dataclass
class RedditComment:
    id: str
    score: int
    body: str
    year: int
    month: int

    def to_json(self) -> JsonDict:
        return asdict(self)

    def __str__(self):
        return f"RedditComment(id={self.id}, score={self.score}, body={self.body}, year={self.year}, month={self.month})"


@dataclass
class RedditPost:
    id: str
    title: str
    original_title: str
    score: int
    url: str
    content: str
    comments: List[RedditComment]
    year: int
    month: int
    subreddit_name: str
    created_at_year: Optional[int] = None
    created_at_month: Optional[int] = None
    created_at_day: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.comments, str):
            if self.comments == "[]":
                self.comments = []
            else:
                logger.warning(
                    f"Comments is a string: {self.comments}, setting to empty list"
                )
                self.comments = []
        elif isinstance(self.comments, list):
            self.comments = [
                RedditComment(**comment) if isinstance(comment, dict) else comment
                for comment in self.comments
            ]
        else:
            logger.warning(
                f"Comments is of unexpected type: {type(self.comments)}, setting to empty list"
            )
            self.comments = []

        logger.info(f"Comments after processing: {self.comments}")

        # Process other fields
        self.subreddit_name = self.subreddit_name.lower()
        date_time = datetime.now()
        self.created_at_year = int(date_time.year)
        self.created_at_month = int(date_time.month)
        self.created_at_day = int(date_time.day)
        self.year = int(self.year)
        self.month = int(self.month)
        self.score = int(self.score)

    def __str__(self):
        return f"RedditPost(id={self.id}, title={self.title}, original_title={self.original_title}, score={self.score}, url={self.url}, content={self.content}, comments={self.comments}, year={self.year}, month={self.month}, subreddit_name={self.subreddit_name}, created_at_year={self.created_at_year}, created_at_month={self.created_at_month}, created_at_day={self.created_at_day})"

    def to_json(self) -> JsonDict:
        data = asdict(self)
        data["comments"] = [comment.to_json() for comment in self.comments]
        return data


@dataclass
class SubredditData:
    subreddit: str
    post_count: int
    posts: List[RedditPost]

    def __post_init__(self):
        if isinstance(self.posts, list):
            self.posts = [
                (
                    post
                    if isinstance(post, RedditPost)
                    else RedditPost(**post, subreddit_name=self.subreddit)
                )
                for post in self.posts
            ]

    def __str__(self):
        return f"SubredditData(subreddit={self.subreddit}, post_count={self.post_count}"

    def to_json(self) -> JsonDict:
        data = asdict(self)
        data["posts"] = [post.to_json() for post in self.posts]
        return data


@dataclass
class TimestampData:
    year: int
    month: int


class TimeFilter(Enum):
    ALL = "all"
    DAY = "day"
