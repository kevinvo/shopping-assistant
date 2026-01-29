"""Reddit indexing job orchestration."""

import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from chalicelib.core.logger_config import setup_logger
from chalicelib.ingestion.reddit.athena import AthenaQueryExecutor
from chalicelib.ingestion.reddit.chunker import RedditChunker
from chalicelib.indexers import IndexerFactory


logger = setup_logger(__name__)


def run_full_indexer(recreate_index: bool = True) -> Dict[str, Any]:
    """
    Run a full reindex of ALL Reddit data from Athena.

    Args:
        recreate_index: If True, deletes and recreates the index before indexing.

    Returns:
        Dict with status and statistics.
    """
    start_time = time.time()
    logger.info("Starting FULL Reddit data indexing process")

    reddit_chunker = RedditChunker()
    indexer = IndexerFactory.create_indexer()

    # Optionally recreate the index
    if recreate_index:
        logger.info("Recreating index from scratch...")
        if hasattr(indexer, "delete_index"):
            indexer.delete_index()
        if hasattr(indexer, "create_index"):
            indexer.create_index()
        logger.info("Index recreated successfully")

    total_posts = 0
    total_documents = 0
    athena_query_executor = AthenaQueryExecutor()

    try:
        # Fetch ALL data (no date filter)
        for batch_num, reddit_posts in enumerate(
            athena_query_executor.fetch_all_data(),
            start=1,
        ):
            batch_start = time.time()
            logger.info(
                "Processing batch %s with %s posts", batch_num, len(reddit_posts)
            )

            reddit_documents = []
            for post_num, reddit_post in enumerate(reddit_posts, start=1):
                try:
                    documents = reddit_chunker.chunk_reddit_post(post=reddit_post)
                    reddit_documents.extend(documents)
                    total_posts += 1
                except Exception as exc:
                    logger.error(
                        "Error processing post %s in batch %s: %s",
                        post_num,
                        batch_num,
                        exc,
                    )

            total_documents += len(reddit_documents)
            logger.info(
                "Indexing batch %s with %s documents", batch_num, len(reddit_documents)
            )

            try:
                indexer.index_documents(docs=reddit_documents)
                batch_duration = time.time() - batch_start
                logger.info(
                    "Batch %s completed in %.2f seconds (total posts: %s, total docs: %s)",
                    batch_num,
                    batch_duration,
                    total_posts,
                    total_documents,
                )
            except Exception as exc:
                logger.error("Error indexing batch %s: %s", batch_num, exc)

        total_duration = time.time() - start_time
        logger.info(
            "FULL indexing completed. Processed %s posts into %s documents in %.2f seconds",
            total_posts,
            total_documents,
            total_duration,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Full reindex success",
                    "total_posts": total_posts,
                    "total_documents": total_documents,
                    "duration_seconds": total_duration,
                }
            ),
        }

    except Exception as exc:
        logger.error("Fatal error in run_full_indexer: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": str(exc),
                    "total_posts_processed": total_posts,
                    "total_documents_processed": total_documents,
                }
            ),
        }


def run_daily_indexer() -> Dict[str, Any]:
    """Run the daily Reddit indexing pipeline."""

    start_time = time.time()
    logger.info("Starting Reddit data indexing process")

    reddit_chunker = RedditChunker()
    indexer = IndexerFactory.create_indexer()

    total_posts = 0
    total_documents = 0
    athena_query_executor = AthenaQueryExecutor()
    two_days_ago = datetime.now() + (timedelta(days=1) - timedelta(days=2))

    try:
        for batch_num, reddit_posts in enumerate(
            athena_query_executor.fetch_data_by(day_ago=two_days_ago),
            start=1,
        ):
            batch_start = time.time()
            logger.info(
                "Processing batch %s with %s posts", batch_num, len(reddit_posts)
            )

            reddit_documents = []
            for post_num, reddit_post in enumerate(reddit_posts, start=1):
                try:
                    documents = reddit_chunker.chunk_reddit_post(post=reddit_post)
                    reddit_documents.extend(documents)
                    total_posts += 1
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.error(
                        "Error processing post %s in batch %s: %s",
                        post_num,
                        batch_num,
                        exc,
                    )

            total_documents += len(reddit_documents)
            logger.info(
                "Indexing batch %s with %s documents", batch_num, len(reddit_documents)
            )

            try:
                indexer.index_documents(docs=reddit_documents)
                batch_duration = time.time() - batch_start
                logger.info(
                    "Batch %s completed in %.2f seconds", batch_num, batch_duration
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Error indexing batch %s: %s", batch_num, exc)

        total_duration = time.time() - start_time
        logger.info(
            "Indexing completed. Processed %s posts into %s documents in %.2f seconds",
            total_posts,
            total_documents,
            total_duration,
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

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Fatal error in run_daily_indexer: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": str(exc),
                    "total_posts_processed": total_posts,
                    "total_documents_processed": total_documents,
                }
            ),
        }
