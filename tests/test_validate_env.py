"""Tests for environment/config validation and operator CLI helpers."""

import os
import tempfile
from pathlib import Path

import pytest

from src.cli.validate_env import validate_environment, ValidationResult


def test_validate_fails_when_config_missing():
    result = validate_environment(config_path=Path("/nonexistent/config.yaml"))
    assert result.ok is False
    assert any("Config not found" in e or "nonexistent" in e for e in result.errors)


def test_validate_fails_when_config_invalid(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("mode: paper\ninvalid: [")
    result = validate_environment(config_path=bad)
    assert result.ok is False
    assert len(result.errors) >= 1


def test_validate_succeeds_with_minimal_valid_config(tmp_path):
    """With valid config, writable dirs, and .env with keys when mode=paper."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "config.yaml"
    cfg.write_text("""
mode: paper
exchange:
  testnet: true
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
database_path: """ + str(tmp_path / "data" / "bot.db").replace("\\", "/") + """
""")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("BYBIT_API_KEY=key\nBYBIT_API_SECRET=secret\nBYBIT_TESTNET=true\n")
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        from src.config.config import load_config
        config, _ = load_config(cfg)
        Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)
        for d in ("artifacts", "artifacts/burnin", "artifacts/validation", "logs"):
            Path(d).mkdir(parents=True, exist_ok=True)
        result = validate_environment(config_path=cfg, require_api_keys_for_live=True)
        assert result.ok is True, result.errors
        assert len(result.errors) == 0
    finally:
        os.chdir(orig)


def test_validate_warns_on_mode_phase_mismatch(tmp_path):
    """Warns when mode=live but burn_in_phase=testnet."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "config.yaml"
    cfg.write_text("""
mode: live
exchange:
  testnet: false
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
burn_in:
  burn_in_enabled: true
  burn_in_phase: testnet
database_path: """ + str(tmp_path / "data" / "bot.db").replace("\\", "/") + """
""")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / ".env").write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\nBYBIT_TESTNET=false\n")
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        Path("artifacts/burnin").mkdir(parents=True, exist_ok=True)
        Path("artifacts/validation").mkdir(parents=True, exist_ok=True)
        result = validate_environment(config_path=cfg, require_api_keys_for_live=True)
        assert any("testnet" in w.lower() and "live" in w.lower() for w in result.warnings) or \
               any("phase" in w.lower() for w in result.warnings)
    finally:
        os.chdir(orig)


def test_validate_errors_when_env_missing_for_paper_mode(tmp_path):
    """When mode=paper and require_api_keys=True, missing .env is an error."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "config.yaml"
    cfg.write_text("""
mode: paper
exchange:
  testnet: true
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
database_path: """ + str(tmp_path / "data" / "bot.db").replace("\\", "/") + """
""")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        Path("artifacts/burnin").mkdir(parents=True, exist_ok=True)
        Path("artifacts/validation").mkdir(parents=True, exist_ok=True)
        result = validate_environment(config_path=cfg, require_api_keys_for_live=True)
        assert result.ok is False
        assert any(".env" in e for e in result.errors)
    finally:
        os.chdir(orig)


def test_validate_cli_invokes_validate():
    """run_bot.py validate exits 0 or 1 and does not crash."""
    root = Path(__file__).resolve().parents[1]
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        pytest.skip("config/config.yaml not present")
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "run_bot.py", "validate", "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert r.returncode in (0, 1)
    if r.returncode == 1:
        assert "ERROR" in r.stdout or "Validation failed" in r.stdout or "error" in r.stderr.lower()
    if r.returncode == 0:
        assert "Validation OK." in r.stdout
        assert "Ready for" in r.stdout


def test_show_runtime_mode_cli():
    """run_bot.py show-runtime-mode runs when config exists."""
    root = Path(__file__).resolve().parents[1]
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        pytest.skip("config/config.yaml not present")
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "run_bot.py", "show-runtime-mode", "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert r.returncode == 0
    assert "mode:" in r.stdout or "burn_in" in r.stdout or "exchange" in r.stdout


def test_runbook_cli_parser_paths():
    """Runbook-documented CLI commands are accepted (evaluate, config rollback, promote status)."""
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        pytest.skip("config/config.yaml not present")
    r = subprocess.run(
        [sys.executable, "run_bot.py", "evaluate", "--config", str(config_path)],
        capture_output=True, text=True, cwd=root,
    )
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [sys.executable, "run_bot.py", "config", "rollback", "--reason", "test", "--config", str(config_path)],
        capture_output=True, text=True, cwd=root,
    )
    assert r.returncode in (0, 1)
    r = subprocess.run(
        [sys.executable, "run_bot.py", "promote", "status", "--config", str(config_path)],
        capture_output=True, text=True, cwd=root,
    )
    assert r.returncode == 0
