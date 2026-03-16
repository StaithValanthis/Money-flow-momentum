"""Tests for Demo init outer retry loop (exit 21, config, script, journal)."""

import os
from pathlib import Path

import pytest

from src.config.config import WarmStartConfig
from src.journal.logger import get_journal_log_path


def test_demo_init_exit_21_when_no_passable_and_retry_enabled(tmp_path: Path, monkeypatch) -> None:
    """Demo init exits with 21 (retryable) when no passable config and retry_init_until_passable True."""
    from typer.testing import CliRunner

    config_path = tmp_path / "config.demo.yaml"
    config_path.write_text("""
operating_mode: demo_research
database_path: %s
artifacts_root: %s
instance_name: demo
warm_start:
  enabled: true
  require_viable_seed_before_trading: true
  retry_init_until_passable: true
  max_init_retry_attempts: 0
  retry_init_sleep_seconds: 60
""" % (str(tmp_path / "demo.db").replace("\\", "/"), str(tmp_path / "artifacts").replace("\\", "/")), encoding="utf-8")

    def fake_run_demo_init(*args, **kwargs):
        return {
            "success": False,
            "viable_seed_found": False,
            "skipped": False,
            "reason": "no_viable_seed_search_exhausted",
            "resumed_from_checkpoint": False,
        }

    monkeypatch.setattr("src.warm_start.run_demo_init", fake_run_demo_init)
    from src.main import app
    runner = CliRunner()
    result = runner.invoke(app, ["demo", "init", "--config", str(config_path)])
    assert result.exit_code == 21, "Expected exit 21 (retryable) when no passable config and retry enabled"


def test_demo_init_exit_1_when_max_retries_reached(tmp_path: Path, monkeypatch) -> None:
    """Demo init exits with 1 when no passable config but attempt >= max_init_retry_attempts."""
    from typer.testing import CliRunner

    config_path = tmp_path / "config.demo.yaml"
    config_path.write_text("""
operating_mode: demo_research
database_path: %s
artifacts_root: %s
instance_name: demo
warm_start:
  enabled: true
  require_viable_seed_before_trading: true
  retry_init_until_passable: true
  max_init_retry_attempts: 3
  retry_init_sleep_seconds: 60
""" % (str(tmp_path / "demo.db").replace("\\", "/"), str(tmp_path / "artifacts").replace("\\", "/")), encoding="utf-8")

    def fake_run_demo_init(*args, **kwargs):
        return {
            "success": False,
            "viable_seed_found": False,
            "skipped": False,
            "reason": "no_viable_seed_search_exhausted",
            "resumed_from_checkpoint": False,
        }

    monkeypatch.setattr("src.warm_start.run_demo_init", fake_run_demo_init)
    from src.main import app
    runner = CliRunner()
    monkeypatch.setenv("DEMO_INIT_ATTEMPT", "3")
    result = runner.invoke(app, ["demo", "init", "--config", str(config_path)])
    assert result.exit_code == 1, "Expected exit 1 when max retry attempts reached"


def test_demo_init_retry_config_cli(tmp_path: Path) -> None:
    """demo init-retry-config prints sleep_seconds and max_attempts (one per line)."""
    from typer.testing import CliRunner
    from src.main import app
    config_path = tmp_path / "config.demo.yaml"
    config_path.write_text("""
operating_mode: demo_research
database_path: %s
artifacts_root: %s
warm_start:
  retry_init_sleep_seconds: 120
  max_init_retry_attempts: 5
""" % (str(tmp_path / "demo.db").replace("\\", "/"), str(tmp_path / "artifacts").replace("\\", "/")), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["demo", "init-retry-config", "--config", str(config_path)])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert len(lines) >= 2
    assert int(lines[0]) == 120
    assert int(lines[1]) == 5


def test_journal_retry_events_written_on_exit_21(tmp_path: Path, monkeypatch) -> None:
    """When demo init would exit 21, journal receives AUTO_RETRY init_retry_scheduled."""
    from typer.testing import CliRunner
    config_path = tmp_path / "config.demo.yaml"
    art = tmp_path / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    config_path.write_text("""
operating_mode: demo_research
database_path: %s
artifacts_root: %s
instance_name: demo
warm_start:
  enabled: true
  require_viable_seed_before_trading: true
  retry_init_until_passable: true
  max_init_retry_attempts: 0
  retry_init_sleep_seconds: 300
""" % (str(tmp_path / "demo.db").replace("\\", "/"), str(art).replace("\\", "/")), encoding="utf-8")

    def fake_run_demo_init(*args, **kwargs):
        return {
            "success": False,
            "viable_seed_found": False,
            "skipped": False,
            "reason": "no_viable_seed_search_exhausted",
            "resumed_from_checkpoint": False,
        }

    monkeypatch.setattr("src.warm_start.run_demo_init", fake_run_demo_init)
    from src.main import app
    runner = CliRunner()
    result = runner.invoke(app, ["demo", "init", "--config", str(config_path)])
    assert result.exit_code == 21
    journal_path = get_journal_log_path(str(art))
    assert journal_path.exists()
    content = journal_path.read_text(encoding="utf-8")
    assert "AUTO_RETRY: init_retry_scheduled" in content
    assert "sleep_seconds=300" in content or "300" in content


def test_retry_config_defaults() -> None:
    """WarmStartConfig has retry fields with expected defaults."""
    w = WarmStartConfig()
    assert getattr(w, "retry_init_until_passable", None) is False
    assert getattr(w, "retry_init_sleep_seconds", None) == 300
    assert getattr(w, "max_init_retry_attempts", None) == 0
