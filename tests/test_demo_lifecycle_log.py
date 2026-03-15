"""Tests for unified Demo lifecycle log (init, probation, runtime, reinit)."""

from pathlib import Path

import pytest

from src.lifecycle.logger import (
    append_demo_lifecycle_event,
    get_demo_lifecycle_jsonl_path,
    get_demo_lifecycle_log_path,
    write_human_log_line,
)
from src.lifecycle.logger import _lifecycle_dir  # noqa: PLC2701 - for path tests


def test_lifecycle_dir_instance_nesting() -> None:
    """Lifecycle dir is under instance; no double-nesting when artifacts_root already has instance."""
    # artifacts_root = "artifacts", instance = "demo" -> artifacts/demo/lifecycle
    p = _lifecycle_dir("artifacts", "demo")
    assert p == Path("artifacts/demo/lifecycle")
    # artifacts_root = "artifacts/demo", instance = "demo" -> artifacts/demo/lifecycle (not artifacts/demo/demo/lifecycle)
    p2 = _lifecycle_dir("artifacts/demo", "demo")
    assert p2 == Path("artifacts/demo/lifecycle")
    # instance None -> default "demo"
    p3 = _lifecycle_dir("artifacts", None)
    assert p3 == Path("artifacts/demo/lifecycle")


def test_append_event_writes_log_and_jsonl(tmp_path: Path) -> None:
    """append_demo_lifecycle_event writes to both human log and JSONL."""
    root = str(tmp_path / "artifacts")
    append_demo_lifecycle_event(root, "demo", "DEMO_INIT", "started")
    append_demo_lifecycle_event(root, "demo", "WARMUP", "passable_config_found", config_id="cfg-1")

    log_path = get_demo_lifecycle_log_path(root, "demo")
    jsonl_path = get_demo_lifecycle_jsonl_path(root, "demo")
    assert log_path.exists()
    assert jsonl_path.exists()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "DEMO_INIT: started" in lines[0]
    assert "WARMUP: passable_config_found" in lines[1] and "config_id=cfg-1" in lines[1]

    jsonl_lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().splitlines() if l]
    assert len(jsonl_lines) == 2
    import json
    first = json.loads(jsonl_lines[0])
    assert first["phase"] == "DEMO_INIT" and first["event"] == "started" and "timestamp_ms" in first
    second = json.loads(jsonl_lines[1])
    assert second["phase"] == "WARMUP" and second["event"] == "passable_config_found" and second.get("config_id") == "cfg-1"


def test_write_human_log_line(tmp_path: Path) -> None:
    """write_human_log_line appends only to .log."""
    root = str(tmp_path / "artifacts")
    write_human_log_line(root, "demo", "Custom line")
    log_path = get_demo_lifecycle_log_path(root, "demo")
    assert log_path.exists()
    assert "Custom line" in log_path.read_text(encoding="utf-8")
    jsonl_path = get_demo_lifecycle_jsonl_path(root, "demo")
    assert not jsonl_path.exists()


def test_demo_init_writes_lifecycle_start(tmp_path: Path, monkeypatch) -> None:
    """Demo init (run_warm_start_calibration) writes DEMO_INIT started and then init_complete or early exit."""
    from src.config.config import Config, EnvSettings, WarmStartConfig
    from src.storage.db import Database
    from src.storage.migrations import run_stage3_migrations
    from src.warm_start.runner import run_warm_start_calibration

    db_path = tmp_path / "demo.db"
    Database(str(db_path)).close()
    run_stage3_migrations(str(db_path))

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.operating_mode = "demo_research"
    cfg.artifacts_root = str(tmp_path / "artifacts")
    cfg.instance_name = "demo"
    cfg.warm_start = WarmStartConfig(enabled=True, candle_source="local")
    env = EnvSettings()
    env.bybit_env = "demo"

    def _fake_load_config(_path=None):
        return cfg, env

    monkeypatch.setattr("src.warm_start.runner.load_config", _fake_load_config)
    monkeypatch.setattr("src.warm_start.runner.get_effective_operating_mode", lambda c, e: "demo_research")
    monkeypatch.setattr("src.warm_start.runner.is_warm_start_needed", lambda db, c: (True, "needed"))
    # No candle data -> early return after DEMO_INIT started
    monkeypatch.setattr("src.warm_start.runner.load_cached_candles", lambda *a, **k: {})

    result = run_warm_start_calibration(
        demo_db_path=str(db_path),
        config_path=None,
        artifact_dir=tmp_path / "artifacts",
    )
    assert result.get("reason") in ("no_candle_data", "fallback_seed_activated") or result.get("skipped")

    log_path = get_demo_lifecycle_log_path(cfg.artifacts_root, cfg.instance_name)
    assert log_path.exists(), "Lifecycle log should exist after demo init"
    content = log_path.read_text(encoding="utf-8")
    assert "DEMO_INIT: started" in content, "Demo init should write DEMO_INIT started"


def test_probation_lifecycle_events_written(tmp_path: Path) -> None:
    """Probation pass/fail events write to lifecycle log (unit test via direct append)."""
    root = str(tmp_path / "artifacts")
    append_demo_lifecycle_event(
        root, "demo", "PROBATION", "failed",
        config_id="c1",
        reason="stalled",
        failure_reason_type="fail_fast_stalled_poor_metrics",
        metrics={"profit_factor": 0.71, "expectancy": -0.029},
    )
    append_demo_lifecycle_event(root, "demo", "PROBATION", "passed", config_id="c1")
    append_demo_lifecycle_event(root, "demo", "DEMO_BASELINE", "promoted", config_id="c1")

    log_path = get_demo_lifecycle_log_path(root, "demo")
    content = log_path.read_text(encoding="utf-8")
    assert "PROBATION: failed" in content
    assert "fail_fast_stalled_poor_metrics" in content
    assert "PROBATION: passed" in content
    assert "DEMO_BASELINE: promoted" in content


def test_auto_reinit_event_written(tmp_path: Path) -> None:
    """AUTO_REINIT reinit_requested is written to lifecycle log."""
    root = str(tmp_path / "artifacts")
    append_demo_lifecycle_event(root, "demo", "AUTO_REINIT", "reinit_requested")

    log_path = get_demo_lifecycle_log_path(root, "demo")
    content = log_path.read_text(encoding="utf-8")
    assert "AUTO_REINIT: reinit_requested" in content
