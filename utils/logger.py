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

    return logger
