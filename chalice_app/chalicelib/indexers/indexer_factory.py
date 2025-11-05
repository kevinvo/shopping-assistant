from typing import Union, Optional
import os

from chalicelib.logger_config import setup_logger
from enum import Enum  # noqa: E402

from .weaviate_indexer import WeaviateIndexer  # noqa: E402
from .qdrant_indexer import QdrantIndexer  # noqa: E402

logger = setup_logger(__name__)


class IndexerType(Enum):
    WEAVIATE = "weaviate"
    QDRANT = "qdrant"


class IndexerFactory:
    @staticmethod
    def create_indexer(
        indexer_type: Optional[IndexerType] = None,
    ) -> Union[WeaviateIndexer, QdrantIndexer]:
        if indexer_type is None:
            indexer_type = IndexerType(
                os.environ.get("INDEXER_TYPE", IndexerType.QDRANT.value)
            )

        if indexer_type == IndexerType.QDRANT:
            logger.info("Creating Qdrant indexer")
            return QdrantIndexer()
        else:
            logger.info("Creating Weaviate indexer")
            return WeaviateIndexer()

    @staticmethod
    def get_available_indexers():
        return [indexer.value for indexer in IndexerType]


if __name__ == "__main__":
    indexer = IndexerFactory.create_indexer()
    weaviate_indexer = IndexerFactory.create_indexer(indexer_type=IndexerType.WEAVIATE)
    qrant_indexer = IndexerFactory.create_indexer(indexer_type=IndexerType.QDRANT)
    print(f"Available indexers: {IndexerFactory.get_available_indexers()}")
