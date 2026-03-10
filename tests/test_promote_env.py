"""Tests for promote-environment helper (Demo -> Live). No exchange access."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.cli.promote_env import (
    run_promote_env_prechecks,
    apply_promote_env,
    write_promotion_artifact,
    PromoteEnvPrecheckResult,
    _update_env_file_to_live,
    _update_config_burn_in_phase,
    _backup_file,
)
from src.validation.readiness import READINESS_READY_SMALL_LIVE, READINESS_NEEDS_REVIEW


def _minimal_config(tmp_path: Path, burn_in_phase: str = "demo", burn_in_enabled: bool = True) -> Path:
    cfg = tmp_path / "config" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    db_path = (tmp_path / "data" / "bot.db").as_posix().replace("\\", "/")
    cfg.write_text(f"""
mode: paper
exchange:
  testnet: false
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
database_path: {db_path}
burn_in:
  burn_in_enabled: {str(burn_in_enabled).lower()}
  burn_in_phase: {burn_in_phase}
  burn_in_max_trades_per_day: 20
  burn_in_max_notional_usdt: 5000
""", encoding="utf-8")
    return cfg


def _env_file(tmp_path: Path, bybit_env: str = "demo", live_keys: bool = True, demo_keys: bool = True) -> Path:
    env = tmp_path / ".env"
    lines = ["BYBIT_ENV=" + bybit_env]
    if demo_keys:
        lines.append("BYBIT_DEMO_API_KEY=dk")
        lines.append("BYBIT_DEMO_API_SECRET=ds")
    if live_keys:
        lines.append("BYBIT_LIVE_API_KEY=lk")
        lines.append("BYBIT_LIVE_API_SECRET=ls")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env


def _ensure_db_and_readiness(tmp_path: Path, config_path: Path) -> None:
    """Create DB with schema so compute_readiness can run and return READY_FOR_SMALL_LIVE for live_small phase."""
    from src.storage.db import Database
    from src.config.config import load_config
    config, _ = load_config(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)
    from src.config.versioning import ensure_stage3_schema
    ensure_stage3_schema(config.database_path)
    db = Database(config.database_path)
    db.close()


def test_precheck_refuses_when_current_env_not_demo(tmp_path):
    """Helper refuses switch when current env is not Demo."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="live", live_keys=True, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
    finally:
        os.chdir(orig)
    assert result.ok is False
    assert result.current_env == "live"
    assert any("demo" in e.lower() or "only switches" in e.lower() or "Demo" in e for e in result.errors)


def test_precheck_refuses_when_live_credentials_missing(tmp_path):
    """Helper refuses switch when live credentials are missing."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=False, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
    finally:
        os.chdir(orig)
    assert result.ok is False
    assert any("live" in e.lower() and "credential" in e.lower() for e in result.errors)


def test_precheck_refuses_when_burn_in_disabled(tmp_path):
    """Helper refuses when burn-in is not enabled."""
    cfg = _minimal_config(tmp_path, burn_in_enabled=False)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    result = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
    assert result.ok is False
    assert any("burn_in" in e.lower() or "burn-in" in e.lower() for e in result.errors)


def test_precheck_refuses_when_readiness_not_sufficient(tmp_path):
    """Helper refuses when readiness is not READY_FOR_SMALL_LIVE (mocked)."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    # With empty DB and live_small phase, compute_readiness returns READY_FOR_SMALL_LIVE.
    # To test refusal we need NEEDS_REVIEW - e.g. add a gate breach in DB.
    from src.storage.db import Database
    from src.config.config import load_config
    config, _ = load_config(cfg)
    db = Database(config.database_path)
    conn = db._get_conn()
    try:
        conn.execute(
            """INSERT INTO burnin_gate_breaches (ts, gate_name, message) VALUES (?, ?, ?)""",
            (1, "test_gate", "test breach"),
        )
        conn.commit()
    except Exception:
        pass
    db.close()
    result = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
    # Now readiness with live_small phase will still run; if there's a breach we get NEEDS_REVIEW
    if result.readiness and result.readiness.classification != READINESS_READY_SMALL_LIVE:
        assert result.ok is False
        assert any("Readiness" in e or "READY_FOR_SMALL_LIVE" in e for e in result.errors)
    # If DB table doesn't exist or breach not in window, we may still get READY - so allow pass
    # and only assert when we know we have breaches in window. For determinism, patch readiness.
    from unittest.mock import patch
    with patch("src.cli.promote_env.compute_readiness") as mock:
        mock.return_value = type("R", (), {"classification": READINESS_NEEDS_REVIEW, "details": {}, "message": "needs review"})()
        result2 = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
        assert result2.ok is False
        assert any("Readiness" in e or "READY_FOR_SMALL_LIVE" in e for e in result2.errors)


def test_precheck_passes_with_demo_env_live_keys_and_readiness(tmp_path):
    """Precheck passes when env is demo, live keys present, burn-in enabled, and readiness READY_FOR_SMALL_LIVE."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    from unittest.mock import patch
    with patch("src.cli.promote_env.compute_readiness") as mock:
        mock.return_value = type("R", (), {"classification": READINESS_READY_SMALL_LIVE, "details": {"trade_count": 1}, "message": "OK"})()
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
        finally:
            os.chdir(orig)
    assert result.ok is True
    assert result.current_env == "demo"
    assert result.live_credentials_present is True


def test_preview_mode_does_not_modify_files(tmp_path):
    """Preview mode (no --confirm-live) does not modify .env or config."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    env_before = env_path.read_text()
    cfg_before = cfg.read_text()
    from unittest.mock import patch
    with patch("src.cli.promote_env.compute_readiness") as mock:
        mock.return_value = type("R", (), {"classification": READINESS_READY_SMALL_LIVE, "details": {}, "message": "OK"})()
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            precheck = run_promote_env_prechecks(config_path=cfg, env_path=env_path)
        finally:
            os.chdir(orig)
    assert precheck.ok is True
    assert env_path.read_text() == env_before
    assert cfg.read_text() == cfg_before


def test_apply_creates_backups_and_updates_files(tmp_path):
    """With --confirm-live, apply creates backups and updates .env and config."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    env_before = env_path.read_text()
    cfg_before = cfg.read_text()
    ok, report = apply_promote_env(config_path=cfg, env_path=env_path, backup=True)
    assert ok is True
    assert report["new_environment"] == "live"
    assert report["new_burn_in_phase"] == "live_small"
    assert len(report["files_changed"]) == 2
    assert len(report["backups_created"]) >= 1
    assert "BYBIT_ENV=live" in env_path.read_text()
    assert env_path.read_text() != env_before
    assert "live_small" in cfg.read_text()
    assert cfg.read_text() != cfg_before
    # Backups exist
    for b in report["backups_created"]:
        assert Path(b).exists()


def test_apply_with_no_backup_does_not_create_backups(tmp_path):
    """With backup=False, no backup files are created."""
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    ok, report = apply_promote_env(config_path=cfg, env_path=env_path, backup=False)
    assert ok is True
    assert report["backups_created"] == []


def test_write_promotion_artifact_creates_json_and_md(tmp_path):
    """write_promotion_artifact creates JSON and MD in validation dir."""
    report = {
        "timestamp_ms": 12345,
        "previous_environment": "demo",
        "new_environment": "live",
        "previous_burn_in_phase": "demo",
        "new_burn_in_phase": "live_small",
        "files_changed": [".env", "config/config.yaml"],
        "backups_created": [".env.bak.1", "config/config.yaml.bak.1"],
        "reason": "test",
    }
    path_json = write_promotion_artifact(report, base_dir=tmp_path)
    assert path_json.exists()
    assert path_json.suffix == ".json"
    data = json.loads(path_json.read_text())
    assert data["new_environment"] == "live"
    path_md = path_json.parent / ("env_promotion_%s.md" % report["timestamp_ms"])
    assert path_md.exists()
    assert "Environment promotion" in path_md.read_text()


def test_update_env_file_to_live_preserves_other_lines(tmp_path):
    """_update_env_file_to_live sets BYBIT_ENV=live and preserves other lines."""
    env = tmp_path / ".env"
    env.write_text("BYBIT_ENV=demo\nBYBIT_DEMO_API_KEY=dk\nBYBIT_LIVE_API_KEY=lk\n", encoding="utf-8")
    ok, msg = _update_env_file_to_live(env)
    assert ok is True
    text = env.read_text()
    assert "BYBIT_ENV=live" in text
    assert "BYBIT_DEMO_API_KEY=dk" in text
    assert "BYBIT_LIVE_API_KEY=lk" in text


def test_update_config_burn_in_phase(tmp_path):
    """_update_config_burn_in_phase sets burn_in.burn_in_phase to live_small."""
    cfg = _minimal_config(tmp_path, burn_in_phase="demo")
    ok, msg = _update_config_burn_in_phase(cfg, "live_small")
    assert ok is True
    import yaml
    data = yaml.safe_load(cfg.read_text())
    assert data["burn_in"]["burn_in_phase"] == "live_small"


def test_backup_file_creates_timestamped_copy(tmp_path):
    """_backup_file creates a timestamped copy."""
    f = tmp_path / "test.txt"
    f.write_text("content")
    backup = _backup_file(f)
    assert backup is not None
    assert backup.exists()
    assert backup.read_text() == "content"
    assert ".bak." in backup.name


def test_promote_env_cli_preview_exits_zero_and_prints_guidance(tmp_path):
    """run_bot.py promote-env (preview) exits 0 and prints next command."""
    root = Path(__file__).resolve().parents[1]
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    (tmp_path / "run_bot.py").write_text(
        "import sys\nsys.path.insert(0, %r)\nfrom src.main import app\nif __name__ == '__main__': app()\n" % str(root)
    )
    r = subprocess.run(
        [sys.executable, "run_bot.py", "promote-env", "--config", str(cfg), "--env", str(env_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    assert "confirm-live" in r.stdout or "Preview" in r.stdout or "promote-env" in r.stdout
    assert "BYBIT_ENV=live" not in env_path.read_text()


def test_promote_env_cli_confirm_updates_files(tmp_path):
    """run_bot.py promote-env --confirm-live updates .env and config."""
    root = Path(__file__).resolve().parents[1]
    cfg = _minimal_config(tmp_path)
    env_path = _env_file(tmp_path, bybit_env="demo", live_keys=True, demo_keys=True)
    _ensure_db_and_readiness(tmp_path, cfg)
    (tmp_path / "run_bot.py").write_text(
        "import sys\nsys.path.insert(0, %r)\nfrom src.main import app\nif __name__ == '__main__': app()\n" % str(root)
    )
    r = subprocess.run(
        [
            sys.executable, "run_bot.py", "promote-env",
            "--config", str(cfg), "--env", str(env_path),
            "--confirm-live", "--no-backup",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    assert "BYBIT_ENV=live" in env_path.read_text()
    assert "live_small" in cfg.read_text()


def test_existing_validate_workflow_unchanged(tmp_path):
    """Existing validate and show-runtime-mode workflows still work."""
    root = Path(__file__).resolve().parents[1]
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        pytest.skip("config/config.yaml not present")
    r = subprocess.run(
        [sys.executable, "run_bot.py", "validate", "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert r.returncode in (0, 1)
    r2 = subprocess.run(
        [sys.executable, "run_bot.py", "show-runtime-mode", "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert r2.returncode == 0
