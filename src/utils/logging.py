"""Structured logging setup."""

import sys
from typing import Any, Optional

from loguru import logger


def get_logger(name: Optional[str] = None):
    """Get a logger instance. If name provided, returns a bound logger."""
    if name:
        return logger.bind(module=name)
    return logger


def _format_record(record: dict[str, Any]) -> str:
    """Format a log record for console/file. Uses extra['module'] when bound, else 'main'."""
    def _get(r: Any, key: str, default: Any = None) -> Any:
        try:
            return r[key]
        except (TypeError, KeyError):
            return getattr(r, key, default)
    time_val = _get(record, "time")
    time_str = time_val.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if hasattr(time_val, "strftime") else str(time_val or "")
    level = _get(record, "level")
    level_name = level.name if hasattr(level, "name") else str(level or "")
    extra = _get(record, "extra") or {}
    module = extra.get("module", "main") if isinstance(extra, dict) else "main"
    msg = _get(record, "message") or ""
    return (
        f"<green>{time_str}</green> | "
        f"<level>{level_name: <8}</level> | "
        f"<cyan>{module}</cyan> | "
        f"<level>{msg}</level>"
    )


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """Configure loguru with structured output."""
    logger.remove()
    logger.add(sys.stderr, format=_format_record, level=level)
    if log_file:
        logger.add(
            log_file,
            format=_format_record,
            level=level,
            rotation=rotation,
            retention=retention,
        )
