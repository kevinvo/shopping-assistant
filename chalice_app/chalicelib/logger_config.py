import logging
import os


def setup_logger(name: str = None) -> logging.Logger:
    logger = logging.getLogger(name)

    # Only add handlers if they don't already exist
    if not logger.handlers:
        handler = logging.StreamHandler()

        # Create a detailed formatter with timestamp, logger name, level, and message
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Set level based on environment variable or default to INFO
        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        logger.setLevel(level_map.get(log_level, logging.INFO))

        # Log the configured level
        if log_level in level_map:
            logger.debug(f"Logger {name} initialized with level {log_level}")

    return logger
