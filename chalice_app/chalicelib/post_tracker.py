from typing import List
import datetime
from dataclasses import dataclass
import boto3
from boto3.dynamodb.conditions import Key
from chalicelib.logger_config import setup_logger
from chalicelib.config import config

logger = setup_logger(__name__)


@dataclass
class PostRecord:
    post_id: str
    subreddit: str
    pulled_at: str
    ttl: int

    @classmethod
    def create(cls, post_id: str, subreddit: str) -> "PostRecord":
        now = datetime.datetime.now()
        return cls(
            post_id=post_id,
            subreddit=subreddit,
            pulled_at=now.isoformat(),
            ttl=int((now + datetime.timedelta(days=360)).timestamp()),
        )

    def to_dynamodb_item(self) -> dict:
        return {
            "post_id": {"S": self.post_id},
            "subreddit": {"S": self.subreddit},
            "pulled_at": {"S": self.pulled_at},
            "ttl": {"N": str(self.ttl)},
        }


class PostTracker:
    def __init__(self):
        self.dynamodb = boto3.client("dynamodb")
        self.table_name = config.dynamodb_table_name  # Use the table name from config

    def mark_post_as_pullled(self, post_id: str, subreddit: str) -> None:
        """Mark a post as processed in DynamoDB."""
        try:
            record = PostRecord.create(post_id=post_id, subreddit=subreddit)
            self.dynamodb.put_item(Item=record.to_dynamodb_item())
        except Exception as e:
            logger.error(f"Error marking post {post_id} as processed: {e}")

    def is_post_pulled(self, post_id: str, subreddit: str) -> bool:
        """Check if a post has been processed before."""
        try:
            response = self.dynamodb.get_item(
                Key={"post_id": post_id, "subreddit": subreddit}
            )
            return "Item" in response
        except Exception as e:
            logger.error(f"Error checking post {post_id} status: {e}")
            return False

    def get_processed_posts_for_subreddit(self, subreddit: str) -> set[PostRecord]:
        """Get all processed post IDs for a subreddit."""
        try:
            response = self.dynamodb.query(
                IndexName="subreddit-index",
                KeyConditionExpression=Key("subreddit").eq(subreddit),
            )
            return {
                PostRecord(
                    post_id=item["post_id"],
                    subreddit=item["subreddit"],
                    pulled_at=item["pulled_at"],
                    ttl=int(item["ttl"]),
                )
                for item in response.get("Items", [])
            }
        except Exception as e:
            logger.error(f"Error getting processed posts for {subreddit}: {e}")
            return set()

    def batch_mark_posts_as_pulled(self, post_ids: List[str], subreddit: str) -> None:
        try:
            items = []
            for post_id in post_ids:
                record = PostRecord.create(post_id=post_id, subreddit=subreddit)
                items.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": record.to_dynamodb_item(),
                            "ConditionExpression": "attribute_not_exists(post_id) AND attribute_not_exists(subreddit)",
                        }
                    }
                )

            # Process items in batches of 25 (DynamoDB limit)
            for i in range(0, len(items), 25):
                batch = items[i : i + 25]
                try:
                    self.dynamodb.transact_write_items(TransactItems=batch)
                except self.dynamodb.exceptions.TransactionCanceledException:
                    # Some items already existed - this is expected
                    logger.info(
                        f"Some posts were already marked as pulled in batch {i}"
                    )
                    pass

        except Exception as e:
            logger.error(
                f"Error batch marking posts as pulled: {str(e)}", exc_info=True
            )
            raise

    def batch_is_post_pulled(
        self, post_ids: List[str], subreddit: str
    ) -> dict[str, bool]:
        try:
            # Create request items for batch get with proper DynamoDB types
            keys = [
                {"post_id": {"S": post_id}, "subreddit": {"S": subreddit}}
                for post_id in post_ids
            ]

            # BatchGetItem can only handle 100 items at a time
            results = {}
            for i in range(0, len(keys), 100):
                batch_keys = keys[i : i + 100]
                response = self.dynamodb.batch_get_item(
                    RequestItems={self.table_name: {"Keys": batch_keys}}
                )

                # Get the returned items
                returned_items = {
                    item["post_id"][
                        "S"
                    ]: True  # Extract string value from DynamoDB format
                    for item in response.get("Responses", {}).get(self.table_name, [])
                }

                # Update results dictionary
                for key in batch_keys:
                    post_id = key["post_id"]["S"]
                    results[post_id] = returned_items.get(post_id, False)

            return results

        except Exception as e:
            logger.error(f"Error batch checking posts status: {e}", exc_info=True)
            return {post_id: False for post_id in post_ids}  # Return all False on error
