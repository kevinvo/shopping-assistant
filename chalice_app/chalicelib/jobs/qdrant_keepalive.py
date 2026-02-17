"""Keep-alive job to prevent Qdrant free-tier instance from being shut down."""

import logging

logger = logging.getLogger(__name__)


def run_qdrant_keepalive() -> dict:
    """
    Perform a simple search query to keep the Qdrant free-tier instance active.

    Qdrant's free tier requires periodic queries to prevent automatic shutdown.
    This job runs a lightweight search to maintain instance availability.

    Returns:
        dict: Status of the keep-alive operation with result count.
    """
    from chalicelib.indexers import IndexerFactory

    try:
        indexer = IndexerFactory.create_indexer()
        results = indexer.hybrid_search(query="White Leather Sneakers", limit=1)

        logger.info(
            "Qdrant keep-alive successful",
            extra={"results_count": len(results)},
        )

        return {
            "statusCode": 200,
            "body": {
                "message": "Keep-alive successful",
                "results_count": len(results),
            },
        }

    except Exception as e:
        logger.error(f"Qdrant keep-alive failed: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": {
                "message": "Keep-alive failed",
                "error": str(e),
            },
        }
