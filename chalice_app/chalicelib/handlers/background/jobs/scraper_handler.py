from typing import Dict, Any
from chalicelib.lambda_constants import (
    SUBREDDIT_NAMES,
)
from chalicelib.logger_config import setup_logger
from chalicelib.reddit_scraper import scrap_daily_subreddits
from chalicelib.util import create_response
from datetime import datetime

logger = setup_logger(__name__)


def run_daily_scraper() -> Dict[str, Any]:
    """
    Run daily Reddit scraper for all subreddits.

    Returns:
        Response with status code and message
    """
    logger.info("Starting reddit_scraper_partial_daily_lambda")
    logger.info(f"Processing {len(SUBREDDIT_NAMES)} subreddits")

    subreddit_name = None
    successful_subreddits = 0
    failed_subreddits = 0

    try:
        for i, subreddit_name in enumerate(SUBREDDIT_NAMES):
            logger.info(
                f"Processing subreddit {i+1}/{len(SUBREDDIT_NAMES)}: r/{subreddit_name}"
            )

            try:
                start_time = datetime.now()
                result = scrap_daily_subreddits(subreddit_name=subreddit_name)
                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()

                if result:
                    post_count = len(result.get("posts", []))
                    logger.info(
                        f"Successfully scraped r/{subreddit_name}: {post_count} posts in {duration:.2f} seconds"
                    )
                    successful_subreddits += 1
                else:
                    logger.warning(f"No data returned for r/{subreddit_name}")
                    failed_subreddits += 1
            except Exception as e:
                logger.error(
                    f"Error processing subreddit r/{subreddit_name}: {str(e)}",
                    exc_info=True,
                )
                failed_subreddits += 1

        logger.info(
            f"Scraping completed. Successful: {successful_subreddits}, Failed: {failed_subreddits}"
        )

        if failed_subreddits > 0:
            return create_response(
                status_code=207,  # Partial success
                message=f"Scraper completed with partial success. Successful: {successful_subreddits}, Failed: {failed_subreddits}",
            )
        else:
            return create_response(
                status_code=200,
                message=f"Scraper Lambda completed successfully. Processed {successful_subreddits} subreddits.",
            )
    except Exception as e:
        error_msg = (
            f"Error in scraper execution: "
            f"current_subreddit='{subreddit_name if subreddit_name else 'not_started'}', "
            f"error={str(e)}"
        )
        logger.error(error_msg, exc_info=True)
        return create_response(status_code=500, message=f"Error: {str(e)}")


# Legacy lambda_handler for backward compatibility
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler wrapper for backward compatibility."""
    return run_daily_scraper()


if __name__ == "__main__":
    run_daily_scraper()
