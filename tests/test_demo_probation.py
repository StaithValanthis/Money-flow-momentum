"""Tests for Demo probation: historically passable seed -> probation -> validated baseline (Demo-only)."""

from pathlib import Path

import pytest

from src.config.config import Config, DemoProbationConfig
from src.config.versioning import (
    ensure_stage3_schema,
    register_config_version,
    activate_config_version,
    get_active_config_id,
)
from src.demo_probation.store import (
    insert_probation_candidate,
    get_probation_record,
    get_current_probation_status,
    update_probation_state,
    LIFECYCLE_DEMO_PROBATION,
    LIFECYCLE_DEMO_PROBATION_FAILED,
    LIFECYCLE_DEMO_PROBATION_PASSED,
)
from src.demo_probation.evaluator import (
    evaluate_probation,
    apply_probation_result,
    PROBATION_STATUS_IN_PROGRESS,
    PROBATION_STATUS_PASSED,
    PROBATION_STATUS_FAILED,
)
from src.storage.db import Database
from src.storage.migrations import run_stage3_migrations


@pytest.fixture
def demo_db(tmp_path):
    db_path = str(tmp_path / "demo.db")
    run_stage3_migrations(db_path)
    return db_path


@pytest.fixture
def config_with_probation(demo_db):
    cfg = Config()
    cfg.database_path = demo_db
    cfg.demo_probation = DemoProbationConfig(
        enabled=True,
        min_closed_trades=5,
        min_runtime_minutes=1,
        forbid_kill_switch_hit=True,
        max_consecutive_losses=3,
        max_stop_out_rate=0.5,
        min_profit_factor=1.05,
        min_expectancy=0.0,
        auto_promote_probation_pass_to_active_demo=True,
        auto_reject_on_failure=True,
        allow_demo_trading_with_probation_candidate=True,
    )
    return cfg


def test_probation_store_insert_and_get(demo_db, config_with_probation):
    """Probation candidate is stored and retrieved with state DEMO_PROBATION."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    ok = insert_probation_candidate(cid, demo_db)
    assert ok
    rec = get_probation_record(cid, demo_db)
    assert rec is not None
    assert rec["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION
    assert rec["config_id"] == cid
    current = get_current_probation_status(demo_db)
    assert current is not None
    assert current["config_id"] == cid


def test_probation_evaluator_fail_kill_switch(demo_db, config_with_probation):
    """Probation fails when kill switch hit and forbid_kill_switch_hit=True."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    insert_probation_candidate(cid, demo_db)
    started = 1000000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    conn.execute("INSERT INTO kill_switch_events (ts, reason) VALUES (?, ?)", (started + 1000, "test"))
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_FAILED
    assert lifecycle == LIFECYCLE_DEMO_PROBATION_FAILED
    assert "kill_switch_hit" in reasons


def test_probation_evaluator_fail_consecutive_losses(demo_db, config_with_probation):
    """Probation fails when max_consecutive_losses breached (e.g. 5 losses at end)."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    insert_probation_candidate(cid, demo_db)
    started = 1000000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    for i in range(10):
        pnl = -1.0 if i >= 5 else 1.0
        conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started + i * 1000, "BTCUSDT", "Buy", 0.01, 100.0, f"o{i}", f"exit_{i}", pnl, cid),
        )
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_FAILED
    assert "max_consecutive_losses_breach" in reasons or "max_consecutive_losses" in str(reasons)


def test_probation_evaluator_in_progress_insufficient_sample(demo_db, config_with_probation):
    """Probation stays IN_PROGRESS when min_closed_trades not reached."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    insert_probation_candidate(cid, demo_db)
    started = 1000000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    for i in range(2):
        conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started + i * 1000, "BTCUSDT", "Buy", 0.01, 100.0, f"o{i}", f"exit_{i}", 1.0, cid),
        )
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_IN_PROGRESS
    assert "sample_or_runtime_not_reached" in reasons


def test_probation_evaluator_pass(demo_db, config_with_probation):
    """Probation passes when min sample/runtime met and metrics above thresholds."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    insert_probation_candidate(cid, demo_db)
    started = 1000000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    for i in range(10):
        pnl = 2.0 if i % 2 == 0 else -1.0
        conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started + i * 1000, "BTCUSDT", "Buy", 0.01, 100.0, f"o{i}", f"exit_{i}", pnl, cid),
        )
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_PASSED
    assert lifecycle == LIFECYCLE_DEMO_PROBATION_PASSED
    assert "passed" in reasons


def test_probation_failed_marked(demo_db, config_with_probation):
    """On failure, candidate is marked DEMO_PROBATION_FAILED and reasons stored."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    insert_probation_candidate(cid, demo_db)
    apply_probation_result(
        cid, demo_db, config_with_probation,
        PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED,
        ["kill_switch_hit"], {"closed_trades": 0},
    )
    rec = get_probation_record(cid, demo_db)
    assert rec["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION_FAILED
    assert rec["ended_at_ts"] is not None


def test_probation_passed_promoted(demo_db, config_with_probation):
    """On pass with auto_promote, promoted_to_baseline_at_ts is set."""
    ensure_stage3_schema(demo_db)
    cid = register_config_version(
        config_with_probation,
        version="test",
        status="candidate",
        description="test",
        source="manual",
        db_path=demo_db,
    )
    activate_config_version(cid, demo_db, reason="test", manual=False)
    insert_probation_candidate(cid, demo_db)
    apply_probation_result(
        cid, demo_db, config_with_probation,
        PROBATION_STATUS_PASSED, LIFECYCLE_DEMO_PROBATION_PASSED,
        ["passed"], {"closed_trades": 10, "profit_factor": 1.2, "expectancy": 0.5},
    )
    rec = get_probation_record(cid, demo_db)
    assert rec["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION_PASSED
    assert rec.get("promoted_to_baseline_at_ts") is not None


def test_probation_artifact_payload(tmp_path):
    """Probation status artifact payload has expected keys."""
    from src.demo_probation.artifacts import build_probation_status_payload, write_probation_status_artifact
    payload = build_probation_status_payload(
        config_id="abc",
        lifecycle_state=LIFECYCLE_DEMO_PROBATION,
        probation_status=PROBATION_STATUS_IN_PROGRESS,
        metrics={"closed_trades": 5},
        reasons=[],
        started_at_ts=1000,
        updated_at_ts=2000,
        ended_at_ts=None,
        promoted_to_baseline_at_ts=None,
        is_active_baseline=False,
    )
    assert payload["candidate_config_id"] == "abc"
    assert payload["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION
    assert payload["probation_status"] == PROBATION_STATUS_IN_PROGRESS
    assert payload["probation_metrics"]["closed_trades"] == 5
    path = write_probation_status_artifact(str(tmp_path), "demo", payload)
    assert path is not None
    assert (tmp_path / "demo" / "probation" / "demo_probation_status.json").exists()


def test_demo_probation_disabled_no_candidate(demo_db):
    """When demo_probation.enabled=False, no probation row is required for evaluation."""
    cfg = Config()
    cfg.database_path = demo_db
    cfg.demo_probation = DemoProbationConfig(enabled=False)
    run_stage3_migrations(demo_db)
    status, lifecycle, reasons, metrics = evaluate_probation(demo_db, cfg)
    assert "probation_disabled" in reasons or "no_probation_candidate" in reasons


def test_demo_init_registers_probation_candidate_when_enabled(demo_db, config_with_probation, tmp_path):
    """When demo init activates a seed and demo_probation.enabled=True, probation candidate is registered."""
    from src.warm_start.runner import _register_probation_candidate_if_enabled
    ensure_stage3_schema(demo_db)
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    cid = register_config_version(
        config_with_probation,
        version="warm_start_seed",
        description="seed",
        status="candidate",
        source="warm_start",
        db_path=demo_db,
        artifact_dir=tmp_path / "configs",
    )
    activate_config_version(cid, demo_db, reason="warm_start", manual=False)
    _register_probation_candidate_if_enabled(config_with_probation, cid, demo_db)
    rec = get_probation_record(cid, demo_db)
    assert rec is not None
    assert rec["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION
    assert rec["config_id"] == cid
