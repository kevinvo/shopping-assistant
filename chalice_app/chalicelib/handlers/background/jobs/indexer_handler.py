from chalicelib.logger_config import setup_logger
from typing import Dict, Any
import json
from chalicelib.athena_retriever import AthenaQueryExecutor
from chalicelib.reddit_chunker_indexer import RedditChunker
from chalicelib.indexers import IndexerFactory
import time
from datetime import timedelta, datetime

logger = setup_logger(__name__)


def run_daily_indexer() -> Dict[str, Any]:
    """
    Run daily Reddit data indexing process.

    Returns:
        Response with status code and message
    """
    start_time = time.time()
    logger.info("Starting Reddit data indexing process")

    reddit_chunker = RedditChunker()
    indexer = IndexerFactory.create_indexer()

    total_posts = 0
    total_documents = 0
    athena_query_executor = AthenaQueryExecutor()
    two_day_ago = datetime.now() + (timedelta(days=1) - timedelta(days=2))

    try:
        for batch_num, reddit_posts in enumerate(
            athena_query_executor.fetch_data_by(day_ago=two_day_ago), 1
        ):
            batch_start = time.time()
            logger.info(f"Processing batch {batch_num} with {len(reddit_posts)} posts")

            reddit_documents = []
            for post_num, reddit_post in enumerate(reddit_posts, 1):
                try:
                    documents = reddit_chunker.chunk_reddit_post(post=reddit_post)
                    reddit_documents.extend(documents)
                    total_posts += 1
                except Exception as e:
                    logger.error(
                        f"Error processing post {post_num} in batch {batch_num}: {str(e)}"
                    )

            total_documents += len(reddit_documents)
            logger.info(
                f"Indexing batch {batch_num} with {len(reddit_documents)} documents"
            )

            try:
                indexer.index_documents(docs=reddit_documents)
                batch_duration = time.time() - batch_start
                logger.info(
                    f"Batch {batch_num} completed in {batch_duration:.2f} seconds"
                )
            except Exception as e:
                logger.error(f"Error indexing batch {batch_num}: {str(e)}")

        total_duration = time.time() - start_time
        logger.info(
            f"Indexing completed. Processed {total_posts} posts into {total_documents} "
            f"documents in {total_duration:.2f} seconds"
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Success",
                    "total_posts": total_posts,
                    "total_documents": total_documents,
                    "duration_seconds": total_duration,
                }
            ),
        }

    except Exception as e:
        logger.error(f"Fatal error in lambda_handler: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": str(e),
                    "total_posts_processed": total_posts,
                    "total_documents_processed": total_documents,
                }
            ),
        }


# Legacy lambda_handler for backward compatibility
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler wrapper for backward compatibility."""
    return run_daily_indexer()


if __name__ == "__main__":
    run_daily_indexer()
