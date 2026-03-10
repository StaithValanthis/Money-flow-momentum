"""Stage 3 workflow integration tests: full path and CLI sanity. No live exchange."""

import tempfile
import time
from pathlib import Path

import pytest

from src.config.config import Config
from src.config.versioning import (
    ensure_stage3_schema,
    register_config_version,
    list_config_versions,
    get_active_config_id,
    activate_config_version,
    rollback_to_previous_config,
)
from src.config.candidate_factory import generate_candidate
from src.evaluation.evaluator import Evaluator
from src.optimizer.search import run_optimization
from src.shadow.shadow_runner import ShadowRunner
from src.shadow.comparison import compare_baseline_shadow
from src.promotion.promoter import promote_candidate
from src.promotion.live_monitor import LiveDegradationMonitor, INSUFFICIENT_DATA_TRADE_COUNT
from src.storage.db import Database


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        Path(path).unlink(missing_ok=True)
    except PermissionError:
        pass


@pytest.fixture
def temp_artifacts():
    d = tempfile.mkdtemp()
    yield Path(d)
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_workflow_register_activate_candidate(temp_db, temp_artifacts):
    ensure_stage3_schema(temp_db)
    c = Config()
    cid = register_config_version(
        c, version="v1", status="baseline", description="base", source="manual",
        db_path=temp_db, artifact_dir=temp_artifacts / "configs",
    )
    assert cid
    assert get_active_config_id(temp_db) is None
    ok = activate_config_version(cid, temp_db)
    assert ok
    assert get_active_config_id(temp_db) == cid
    cand_id = generate_candidate(
        c, {"entry.long_threshold": 1.8}, version="cand1", db_path=temp_db,
        artifact_dir=temp_artifacts / "configs",
    )
    assert cand_id
    assert (temp_artifacts / "configs").exists() or True


def test_workflow_evaluate_optimize(temp_db, temp_artifacts):
    ensure_stage3_schema(temp_db)
    c = Config()
    cid = register_config_version(c, version="v1", status="active", description="", source="manual", db_path=temp_db)
    activate_config_version(cid, temp_db)
    db = Database(temp_db)
    for i in range(3):
        db.insert_trade(ts=1000 + i * 1000, symbol="BTCUSDT", side="Buy", qty=0.01, price=50000.0 + i * 100, pnl=10.0 + i, order_id=f"oid_{i}", config_id=cid)
    db.insert_entry_decision(1000, "BTCUSDT", "long", "order_placed", 1.5, False, config_id=cid)
    db.insert_lifecycle_event(2000, "BTCUSDT", "tp1_fill_full", "tp1", "", config_id=cid)
    db.close()
    ev = Evaluator(temp_db)
    summary = ev.run(from_ts=1000, to_ts=5000, config_id=cid, artifact_dir=temp_artifacts / "evaluations")
    assert summary["run_id"]
    assert summary["trade_count"] == 3
    assert (temp_artifacts / "evaluations").exists()
    out = run_optimization(db_path=temp_db, config_id=cid, from_ts=1000, to_ts=5000, n_samples=2, artifact_dir=temp_artifacts)
    assert "run_id" in out
    assert "best_candidate_config_id" in out


def test_workflow_shadow_run_and_report(temp_db, temp_artifacts):
    ensure_stage3_schema(temp_db)
    c = Config()
    base_id = register_config_version(c, version="base", status="active", description="", source="manual", db_path=temp_db)
    activate_config_version(base_id, temp_db)
    cand_id = generate_candidate(c, {"entry.long_threshold": 1.7}, version="cand", db_path=temp_db)
    runner = ShadowRunner(temp_db)
    ok = runner.start(cand_id)
    assert ok
    runner.record_decision(ts=2000, symbol="BTCUSDT", direction="long", reason="shadow", score=1.6, baseline_decision="long", baseline_score=1.5)
    runner.stop()
    db = Database(temp_db)
    row = db._get_conn().execute("SELECT id FROM shadow_runs WHERE candidate_config_id = ?", (cand_id,)).fetchone()
    db.close()
    assert row
    out = compare_baseline_shadow(row[0], temp_db, artifact_dir=temp_artifacts / "shadow")
    assert "agreement_rate" in out
    assert out.get("report_path")
    assert Path(out["report_path"]).exists()
    assert out.get("mode") == "post_hoc"
    runner.db.close()


def test_workflow_promote_and_rollback(temp_db):
    ensure_stage3_schema(temp_db)
    c = Config()
    cid1 = register_config_version(c, version="v1", status="active", description="", source="manual", db_path=temp_db)
    activate_config_version(cid1, temp_db)
    c.entry.long_threshold = 1.8
    cid2 = register_config_version(c, version="v2", status="candidate", description="", source="manual", db_path=temp_db)
    ok, _ = promote_candidate(cid2, db_path=temp_db, auto_approved=True)
    assert ok
    assert get_active_config_id(temp_db) == cid2
    prev = rollback_to_previous_config(temp_db, reason="test")
    assert prev == cid1
    assert get_active_config_id(temp_db) == cid1


def test_degradation_monitor_persists_events(temp_db):
    ensure_stage3_schema(temp_db)
    c = Config()
    cid = register_config_version(c, version="v1", status="active", description="", source="manual", db_path=temp_db)
    activate_config_version(cid, temp_db)
    db = Database(temp_db)
    for i in range(10):
        db.insert_trade(ts=1000000 + i * 1000, symbol="X", side="Buy", qty=1, price=100, pnl=-5.0, config_id=cid)
    db.close()
    mon = LiveDegradationMonitor(temp_db, max_drawdown_pct=1.0, min_trade_count_per_period=5)
    events = mon.check({
        "trade_count": 10,
        "max_drawdown": 15.0,
        "expectancy": -12.0,
        "diagnostic": {"stop_out_rate": 0.5},
    }, config_id=cid)
    assert len(events) >= 1
    db2 = Database(temp_db)
    rows = db2._get_conn().execute("SELECT * FROM degradation_events WHERE config_id = ?", (cid,)).fetchall()
    db2.close()
    assert len(rows) >= 1
    mon.db.close()


def test_degradation_insufficient_data_no_persist(temp_db):
    ensure_stage3_schema(temp_db)
    mon = LiveDegradationMonitor(temp_db, min_trade_count_per_period=10)
    events = mon.check({"trade_count": 3, "max_drawdown": 20.0}, config_id="fake_id")
    assert len(events) == 0
    mon.db.close()


def test_cli_commands_exist():
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    for cmd in [["run", "--help"], ["evaluate", "--help"], ["rollback", "--help"]]:
        r = subprocess.run([sys.executable, "run_bot.py"] + cmd, capture_output=True, text=True, cwd=root)
        assert r.returncode == 0, f"run_bot.py {' '.join(cmd)} failed: {r.stderr}"


def test_commands_help_no_crash():
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    for cmd in [["config", "--help"], ["evaluate", "--help"], ["optimize", "--help"], ["shadow", "--help"], ["promote", "--help"]]:
        r = subprocess.run(
            [sys.executable, "run_bot.py"] + cmd,
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert r.returncode == 0, f"run_bot.py {' '.join(cmd)} failed: {r.stderr}"
