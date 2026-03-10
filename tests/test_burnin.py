"""Burn-in / validation mode tests. No live exchange."""

import tempfile
import time
from pathlib import Path

import pytest
from src.config.config import Config, BurnInConfig
from src.validation.burn_in import check_burnin_gates, BurnInGateResult
from src.validation.readiness import compute_readiness, READINESS_NOT_READY, READINESS_READY_TESTNET, READINESS_NEEDS_REVIEW
from src.storage.db import Database


def _temp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    db = Database(path)
    db.close()
    return path


def test_burnin_gates_block_entries_when_limits_breached():
    """Burn-in gates block entries when limits are breached."""
    config = Config()
    config.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_max_trades_per_day=2)
    db_path = _temp_db()
    try:
        db = Database(db_path)
        result = check_burnin_gates(config, db, trades_today=3, config_id="test")
        assert result.blocked_entries is True
        assert any(b["gate"] == "burn_in_max_trades_per_day" for b in result.breaches)
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_burnin_gates_pass_when_disabled():
    """When burn_in_enabled=False, gates pass."""
    config = Config()
    config.burn_in = BurnInConfig(burn_in_enabled=False)
    result = check_burnin_gates(config, None, trades_today=100)
    assert result.passed is True
    assert result.blocked_entries is False


def test_readiness_classification_good_vs_bad():
    """Readiness classification behaves correctly."""
    db_path = _temp_db()
    try:
        db = Database(db_path)
        r = compute_readiness(db, window_hours=24)
        assert r.classification in (READINESS_NOT_READY, READINESS_READY_TESTNET, READINESS_NEEDS_REVIEW)
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_readiness_with_kill_switch_not_ready():
    """Readiness is NOT_READY when kill switch in window."""
    db_path = _temp_db()
    try:
        db = Database(db_path)
        db._get_conn().execute("INSERT INTO kill_switch_events (ts, reason) VALUES (?, ?)", (int(time.time() * 1000) - 1000, "test"))
        db._get_conn().commit()
        r = compute_readiness(db, window_hours=24)
        assert r.classification == READINESS_NOT_READY
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_execution_audit_detects_size_mismatch():
    """Execution audit records and update_on_fill can detect size delta."""
    db_path = _temp_db()
    try:
        db = Database(db_path)
        ts = int(time.time() * 1000)
        db.insert_execution_audit(ts=ts, symbol="X", side="Buy", intent_qty=1.0, intent_price=100.0, order_id="o1", ack_ts=ts, config_id="c1")
        db.update_execution_audit_on_fill("o1", fill_qty=0.9, fill_price=100.0, fill_ts=ts + 100, size_delta=-0.1, mismatch_reason="size_delta")
        rows = db.get_execution_audit(order_id="o1")
        assert len(rows) == 1
        assert rows[0].get("mismatch_reason") == "size_delta"
        assert rows[0].get("size_delta") == -0.1
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_protection_audit_detects_missing_sl():
    """Protection audit can record missing stop loss."""
    db_path = _temp_db()
    try:
        db = Database(db_path)
        ts = int(time.time() * 1000)
        db.insert_protection_audit(ts, "BTCUSDT", "missing_stop_loss", expected_value=99.0, actual_value=0.0, repaired=0, message="Missing SL", config_id="c1")
        rows = db.get_protection_audit(since_ts=ts - 1)
        assert len(rows) == 1
        assert rows[0]["check_type"] == "missing_stop_loss"
        assert rows[0]["expected_value"] == 99.0
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_burnin_commands_handle_missing_data(tmp_path):
    """status/report/burnin commands handle missing or partial data."""
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    for cmd in [["burnin", "status"], ["burnin", "report"], ["burnin", "readiness"]]:
        r = subprocess.run([sys.executable, "run_bot.py"] + cmd, capture_output=True, text=True, cwd=root)
        assert r.returncode == 0, f"{cmd}: {r.stderr}"


def test_fill_quality_metrics_aggregate():
    """Slippage metrics aggregate correctly from sample audit rows."""
    from src.evaluation.metrics import compute_fill_quality_metrics
    rows = [
        {"slippage_bps": 10.0, "ack_ts": 1000, "fill_ts": 1100, "size_delta": 0, "mismatch_reason": None},
        {"slippage_bps": 20.0, "ack_ts": 2000, "fill_ts": 2050, "size_delta": 0.1, "mismatch_reason": "size_delta"},
    ]
    m = compute_fill_quality_metrics(rows)
    assert m["avg_entry_slippage_bps"] == 15.0
    assert m["execution_drift_count"] == 1
    assert m["audit_record_count"] == 2


def test_burnin_artifacts_written(tmp_path):
    """Burn-in readiness writes JSON and MD to output dir."""
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "burnin_out"
    r = subprocess.run(
        [sys.executable, "run_bot.py", "burnin", "readiness", "--output", str(out)],
        capture_output=True, text=True, cwd=root,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    files = list(out.glob("readiness_*.json")) + list(out.glob("readiness_*.md"))
    assert len(files) >= 2


def test_burnin_mode_does_not_break_non_burnin():
    """With burn_in_enabled=False, entry path is not blocked."""
    config = Config()
    config.burn_in = BurnInConfig(burn_in_enabled=False)
    result = check_burnin_gates(config, None, trades_today=50, notional_today_usdt=100_000)
    assert result.passed is True
    assert not result.breaches
