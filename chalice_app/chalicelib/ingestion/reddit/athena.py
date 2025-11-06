import os
import pprint
from datetime import datetime, timedelta
from typing import List, Generator, Tuple, Optional

from pyathena import connect

from chalicelib.core.logger_config import setup_logger
from chalicelib.models.data_objects import RedditPost

logger = setup_logger(__name__)

ATHENA_OUTPUT_BUCKET = os.environ.get(
    "ATHENA_OUTPUT_BUCKET",
    "redditscraperstack-athenaqueryresultsbucketae74152-mgcdqkp31f3b",
)

ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE", "reddit_data")
TABLE_NAME = os.environ.get("TABLE_NAME", "merged_data")
PAGE_SIZE = 100


class AthenaQueryExecutor:
    def __init__(self):
        self.conn = connect(
            s3_staging_dir=f"s3://{ATHENA_OUTPUT_BUCKET}/", region_name="ap-southeast-1"
        )
        self.page_size = PAGE_SIZE

    def refresh_partitions(self):
        """
        Run MSCK REPAIR TABLE to refresh partition metadata.
        This ensures Athena is aware of all partitions in S3.
        """
        try:
            logger.info(f"Refreshing partitions for {ATHENA_DATABASE}.{TABLE_NAME}")
            repair_query = f"MSCK REPAIR TABLE {ATHENA_DATABASE}.{TABLE_NAME}"
            cursor = self.conn.cursor()
            cursor.execute(repair_query)
            cursor.close()
            logger.info("Partition refresh completed successfully")
        except Exception as e:
            logger.error(f"Error refreshing partitions: {e}", exc_info=True)

    def fetch_all_data(
        self, where_clause: str = "", total_size: Optional[int] = None
    ) -> Generator[List[RedditPost], None, None]:

        count = 0
        total_size_per_reddit_post = min(
            [x for x in [total_size, self.page_size] if x is not None]
        )
        try:
            where_clause = f"{where_clause} ORDER BY created_at_year asc, created_at_month asc, created_at_day asc"
            query = f"SELECT * FROM {ATHENA_DATABASE}.{TABLE_NAME} {where_clause}"  # nosec B608 - table names are from env vars
            logger.info(f"query: {query}")
            cursor = self.conn.cursor()
            cursor.execute(query)
            while True:
                batch: List[Tuple] = cursor.fetchmany(size=total_size_per_reddit_post)
                if not batch:
                    break
                reddit_posts = []
                for row in batch:
                    row_dict = dict(zip([desc[0] for desc in cursor.description], row))
                    reddit_post = RedditPost(**row_dict)
                    reddit_posts.append(reddit_post)
                count += len(reddit_posts)
                if total_size and count >= total_size:
                    yield reddit_posts
                    break
                yield reddit_posts
        except Exception as e:
            logger.error(f"Error querying Athena: {e}", exc_info=True)
        finally:
            cursor.close()
            self.conn.close()

    def fetch_data_by(
        self,
        total_size: Optional[int] = None,
        day_ago: datetime = datetime.now() - timedelta(days=5),
    ):
        # Refresh partitions before querying to ensure all data is visible
        self.refresh_partitions()

        where_clause = (
            f"WHERE created_at_year >= '{day_ago.year}' "
            f"AND created_at_month >= '{day_ago.month}' "
            f"AND created_at_day >= '{day_ago.day}' "
        )
        logger.info(f"Total Size Requested: {total_size}")
        logger.info(f"Day Ago: {day_ago}")
        for reddit_posts in self.fetch_all_data(
            where_clause=where_clause, total_size=total_size
        ):
            logger.info(f"Batch Length: {len(reddit_posts)}")
            yield reddit_posts


# Example usage
if __name__ == "__main__":
    pp = pprint.PrettyPrinter(indent=4)
    documents = AthenaQueryExecutor().fetch_data_by(total_size=2)
    for document in documents:
        print(document)
