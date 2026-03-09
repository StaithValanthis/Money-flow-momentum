"""Structured logging setup."""

import sys
from typing import Optional

from loguru import logger


def get_logger(name: Optional[str] = None):
    """Get a logger instance. If name provided, returns a bound logger."""
    if name:
        return logger.bind(module=name)
    return logger


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """Configure loguru with structured output."""
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra.get('module', 'main')}</cyan> | "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, format=fmt, level=level)
    if log_file:
        logger.add(
            log_file,
            format=fmt,
            level=level,
            rotation=rotation,
            retention=retention,
        )
