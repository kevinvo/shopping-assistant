"""LangSmith dataset logging for customer queries."""

import logging
from datetime import datetime
from typing import Dict, Any, Optional
from langsmith import Client

from chalicelib.core.config import config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize LangSmith client
langsmith_client = Client(
    api_key=config.langsmith_api_key,
    api_url=config.langsmith_api_url,
)

DATASET_NAME = "customer-queries"


class QueryLogger:
    """Log customer queries to LangSmith dataset for tracking and analysis."""

    def __init__(self, dataset_name: str = DATASET_NAME):
        self.dataset_name = dataset_name
        self.client = langsmith_client
        self._ensure_dataset_exists()

    def _ensure_dataset_exists(self) -> None:
        try:
            # Try to get the dataset
            self.client.read_dataset(dataset_name=self.dataset_name)
            logger.info(f"Dataset '{self.dataset_name}' already exists")
        except Exception:
            # Dataset doesn't exist, create it
            try:
                self.client.create_dataset(
                    dataset_name=self.dataset_name,
                    description="Customer queries with session tracking for the shopping assistant",
                )
                logger.info(f"Created dataset '{self.dataset_name}'")
            except Exception as e:
                logger.warning(f"Could not create dataset: {e}")

    def log_query(
        self,
        query: str,
        session_id: str,
        response: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            # Prepare metadata
            log_metadata = {
                "session_id": session_id,
                "timestamp": datetime.utcnow().isoformat(),
                "query_length": len(query),
            }

            if metadata:
                log_metadata.update(metadata)

            # Create example in dataset
            self.client.create_example(
                inputs={"query": query},
                outputs={"response": response} if response else None,
                dataset_name=self.dataset_name,
                metadata=log_metadata,
            )

            logger.info(f"Logged query to dataset for session {session_id}")

        except Exception as e:
            # Don't fail the request if logging fails
            logger.warning(f"Failed to log query to dataset: {e}")


# Singleton instance
query_logger = QueryLogger()


def log_customer_query(
    query: str,
    session_id: str,
    response: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    query_logger.log_query(
        query=query,
        session_id=session_id,
        response=response,
        metadata=metadata,
    )
