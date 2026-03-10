"""Tests for logging setup: format does not use invalid Loguru expressions."""

import pytest

from src.utils.logging import setup_logging, get_logger, _format_record


def test_format_record_with_extra_module():
    """_format_record uses extra['module'] when present and does not raise."""
    from datetime import datetime
    # Dict-like record (Loguru Record supports __getitem__)
    record = {
        "time": datetime(2025, 3, 9, 12, 0, 0, 123000),
        "level": type("Level", (), {"name": "INFO"})(),
        "extra": {"module": "src.main"},
        "message": "test message",
    }
    out = _format_record(record)
    assert "src.main" in out
    assert "test message" in out
    assert "2025-03-09" in out


def test_format_record_without_extra_module():
    """_format_record falls back to 'main' when extra has no module."""
    from datetime import datetime
    record = {
        "time": datetime(2025, 3, 9, 12, 0, 0, 123000),
        "level": type("Level", (), {"name": "INFO"})(),
        "extra": {},
        "message": "no module",
    }
    out = _format_record(record)
    assert "main" in out
    assert "no module" in out


def test_format_record_escapes_braces_in_message():
    """Messages with { or } are escaped so Loguru format_map does not raise KeyError."""
    from datetime import datetime
    record = {
        "time": datetime(2025, 3, 9, 12, 0, 0, 123000),
        "level": type("Level", (), {"name": "INFO"})(),
        "extra": {"module": "test"},
        "message": "Request → GET https://api.example.com/v5/position/list: category=linear&symbol=0GUSDT.",
    }
    out = _format_record(record)
    assert "category=linear" in out
    assert out.endswith("\n")
    # Message with braces: should appear escaped (double braces) so format_map won't KeyError
    record["message"] = "params: {category}"
    out2 = _format_record(record)
    assert "{{category}}" in out2


def test_setup_logging_and_log_no_crash():
    """setup_logging() and a few log lines do not raise (e.g. no extra.get in format)."""
    setup_logging(level="DEBUG")
    log = get_logger("tests.test_logging")
    log.debug("structured log with module")
    # Root logger without bound module
    get_logger().info("root log")
    # Should not raise AttributeError about .get('module', 'main')
