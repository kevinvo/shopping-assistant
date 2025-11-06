from enum import Enum
from typing import List


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


class TimeFilter(Enum):
    ALL = "all"
    DAY = "day"
