import os

from chalicelib.indexers.indexer_factory import IndexerFactory, IndexerType
from chalicelib.logger_config import setup_logger

logger = setup_logger(__name__)


def create_index(force_recreate: bool = False):
    # Get the indexer type from environment variable or use default
    indexer_type = os.environ.get("INDEXER_TYPE", IndexerType.QDRANT.value)
    logger.info(f"Creating index using {indexer_type} indexer")

    try:
        # Create the indexer
        indexer = IndexerFactory.create_indexer(indexer_type)

        # Delete existing index if force_recreate is True
        if force_recreate and hasattr(indexer, "delete_index"):
            logger.info(f"Deleting existing index for {indexer_type}")
            indexer.delete_index()

        # Create the index
        if hasattr(indexer, "create_index"):
            indexer.create_index()
            logger.info(f"Successfully created index using {indexer_type}")
        else:
            logger.warning(
                f"Indexer {indexer_type} does not support create_index method"
            )

    except Exception as e:
        logger.error(f"Error creating index: {e}")
        raise


if __name__ == "__main__":
    # Check if force_recreate flag is set
    force_recreate = os.environ.get("FORCE_RECREATE", "false").lower() == "true"
    create_index(force_recreate=force_recreate)
