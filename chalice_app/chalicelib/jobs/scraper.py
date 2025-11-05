"""Daily Reddit scraping job orchestration."""

from datetime import datetime
from typing import Any, Dict

from chalicelib.core.logger_config import setup_logger
from chalicelib.ingestion.reddit.scraper import scrap_daily_subreddits
from chalicelib.models.lambda_constants import SUBREDDIT_NAMES
from chalicelib.core.http_responses import create_response


logger = setup_logger(__name__)


def run_daily_scraper() -> Dict[str, Any]:
    """Run the Reddit scraper across all configured subreddits."""

    logger.info("Starting reddit_scraper_partial_daily_lambda")
    logger.info("Processing %s subreddits", len(SUBREDDIT_NAMES))

    subreddit_name = None
    successful_subreddits = 0
    failed_subreddits = 0

    try:
        for i, subreddit_name in enumerate(SUBREDDIT_NAMES):
            logger.info(
                "Processing subreddit %s/%s: r/%s",
                i + 1,
                len(SUBREDDIT_NAMES),
                subreddit_name,
            )

            try:
                start_time = datetime.now()
                result = scrap_daily_subreddits(subreddit_name=subreddit_name)
                duration = (datetime.now() - start_time).total_seconds()

                if result:
                    post_count = len(result.get("posts", []))
                    logger.info(
                        "Successfully scraped r/%s: %s posts in %.2f seconds",
                        subreddit_name,
                        post_count,
                        duration,
                    )
                    successful_subreddits += 1
                else:
                    logger.warning("No data returned for r/%s", subreddit_name)
                    failed_subreddits += 1
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "Error processing subreddit r/%s: %s",
                    subreddit_name,
                    exc,
                    exc_info=True,
                )
                failed_subreddits += 1

        logger.info(
            "Scraping completed. Successful: %s, Failed: %s",
            successful_subreddits,
            failed_subreddits,
        )

        if failed_subreddits > 0:
            return create_response(
                status_code=207,
                message=(
                    "Scraper completed with partial success. Successful: "
                    f"{successful_subreddits}, Failed: {failed_subreddits}"
                ),
            )

        return create_response(
            status_code=200,
            message=(
                "Scraper Lambda completed successfully. Processed "
                f"{successful_subreddits} subreddits."
            ),
        )

    except Exception as exc:  # pragma: no cover - defensive logging
        error_msg = (
            "Error in scraper execution: current_subreddit='"
            + (subreddit_name if subreddit_name else "not_started")
            + f"', error={exc}"
        )
        logger.error(error_msg, exc_info=True)
        return create_response(status_code=500, message=str(exc))
