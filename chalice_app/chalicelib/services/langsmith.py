"""LangSmith dataset logging for customer queries."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langsmith import Client

from chalicelib.core.config import config


logger = logging.getLogger()
logger.setLevel(logging.INFO)


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
            self.client.read_dataset(dataset_name=self.dataset_name)
            logger.info("Dataset '%s' already exists", self.dataset_name)
        except Exception:
            try:
                self.client.create_dataset(
                    dataset_name=self.dataset_name,
                    description=(
                        "Customer queries with session tracking for the shopping assistant"
                    ),
                )
                logger.info("Created dataset '%s'", self.dataset_name)
            except Exception as exc:  # pragma: no cover - logging-only path
                logger.warning("Could not create dataset: %s", exc)

    def log_query(
        self,
        query: str,
        session_id: str,
        response: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            log_metadata: Dict[str, Any] = {
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query_length": len(query),
            }

            if metadata:
                log_metadata.update(metadata)

            self.client.create_example(
                inputs={"query": query},
                outputs={"response": response} if response else None,
                dataset_name=self.dataset_name,
                metadata=log_metadata,
            )

            logger.info("Logged query to dataset for session %s", session_id)

        except Exception as exc:  # pragma: no cover - logging-only path
            logger.warning("Failed to log query to dataset: %s", exc)


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
