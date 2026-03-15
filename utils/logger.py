"""Logging configuration using loguru."""
import sys
from loguru import logger


def setup_logger(level: str = "INFO", log_file: str = "bot.log"):
    """Configure loguru for console + file logging."""
    logger.remove()

    # Console: clean human-readable format
    logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File: full format with traceback
    logger.add(
        log_file,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    # Progress log: only INFO+ from main processing steps, no debug noise
    logger.add(
        "progress.log",
        level="INFO",
        rotation="1 MB",
        retention="3 days",
        format="{time:HH:mm:ss} | {level: <8} | {message}",
        filter=lambda record: record["name"] in ("__main__", "market.polymarket_client", "execution.trader"),
    )

    return logger
