"""Tests for Demo probation: historically passable seed -> probation -> validated baseline (Demo-only)."""

import sys
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
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_FAILED
    assert lifecycle == LIFECYCLE_DEMO_PROBATION_FAILED
    assert "kill_switch_hit" in reasons
    assert failure_type in ("fail_fast_kill_switch", "timer_evaluated")


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
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_FAILED
    assert "max_consecutive_losses_breach" in reasons or "max_consecutive_losses" in str(reasons)
    assert failure_type == "fail_fast_consecutive_losses"


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
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_IN_PROGRESS
    assert "sample_or_runtime_not_reached" in reasons or "stall_without_enough_trade_evidence" in reasons
    assert failure_type is None


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
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_PASSED
    assert lifecycle == LIFECYCLE_DEMO_PROBATION_PASSED
    assert "passed" in reasons
    assert failure_type is None


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
        ["kill_switch_hit"], {"closed_trades": 0}, failure_reason_type="fail_fast_kill_switch",
    )
    rec = get_probation_record(cid, demo_db)
    assert rec["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION_FAILED
    assert rec["ended_at_ts"] is not None
    assert rec.get("failure_reason_type") == "fail_fast_kill_switch"


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
    status, lifecycle, reasons, metrics, _ = evaluate_probation(demo_db, cfg)
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


def test_fail_fast_hard_block(demo_db, config_with_probation):
    """Probation fails with fail_fast_hard_block when automation state is BLOCKED or burnin breach."""
    import time as _time
    from src.demo_probation.evaluator import FAILURE_REASON_FAIL_FAST_HARD_BLOCK
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
    db.upsert_automation_state({"state": "BLOCKED_BY_BURNIN", "updated_ts": int(_time.time() * 1000)})
    db.close()
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_FAILED
    assert failure_type == FAILURE_REASON_FAIL_FAST_HARD_BLOCK
    assert "hard_block" in str(reasons).lower()


def test_zero_closed_trades_stalled_does_not_fail_stall_metrics(demo_db, config_with_probation):
    """Zero closed trades + stalled runtime must NOT trigger fail_fast_stalled_poor_metrics (stay IN_PROGRESS)."""
    import time as _time
    config_with_probation.demo_probation.no_trade_stall_minutes = 1
    config_with_probation.demo_probation.min_closed_trades_before_stall_metric_failure = 5
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
    now_ms = int(_time.time() * 1000)
    started = now_ms - 120000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_IN_PROGRESS
    assert failure_type is None
    assert "stall_without_enough_trade_evidence" in reasons
    assert metrics.get("closed_trades", 0) == 0


def test_insufficient_closed_trades_stalled_does_not_fail_stall_metrics(demo_db, config_with_probation):
    """Low but insufficient closed trades + stalled + poor PF/exp must NOT trigger fail_fast_stalled_poor_metrics."""
    import time as _time
    from src.demo_probation.evaluator import FAILURE_REASON_FAIL_FAST_STALLED_POOR_METRICS
    config_with_probation.demo_probation.no_trade_stall_minutes = 1
    config_with_probation.demo_probation.min_closed_trades_before_stall_metric_failure = 5
    config_with_probation.demo_probation.fail_if_stalled_and_pf_below = 0.95
    config_with_probation.demo_probation.fail_if_stalled_and_negative_expectancy = True
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
    now_ms = int(_time.time() * 1000)
    started = now_ms - 120000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    for i in range(3):
        conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started + i * 100, "BTCUSDT", "Buy", 0.01, 100.0, f"o{i}", f"exit_{i}", -0.5, cid),
        )
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_IN_PROGRESS
    assert failure_type is not FAILURE_REASON_FAIL_FAST_STALLED_POOR_METRICS
    assert "stall_without_enough_trade_evidence" in reasons


def test_fail_fast_stalled_poor_metrics(demo_db, config_with_probation):
    """Probation fails with fail_fast_stalled_poor_metrics when enough trades, stall, and PF/expectancy poor."""
    import time as _time
    from src.demo_probation.evaluator import FAILURE_REASON_FAIL_FAST_STALLED_POOR_METRICS
    config_with_probation.demo_probation.no_trade_stall_minutes = 1
    config_with_probation.demo_probation.fail_if_stalled_and_pf_below = 0.95
    config_with_probation.demo_probation.fail_if_stalled_and_negative_expectancy = True
    config_with_probation.demo_probation.min_closed_trades_before_stall_metric_failure = 5
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
    now_ms = int(_time.time() * 1000)
    started = now_ms - 120000
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (started, cid))
    conn.commit()
    pnls = [0.5, -0.5, 0.5, -0.5, -0.5]
    for i in range(5):
        conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started + i * 100, "BTCUSDT", "Buy", 0.01, 100.0, f"o{i}", f"exit_{i}", pnls[i], cid),
        )
    conn.commit()
    db.close()
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert status == PROBATION_STATUS_FAILED
    assert failure_type == FAILURE_REASON_FAIL_FAST_STALLED_POOR_METRICS
    assert "stalled" in str(reasons).lower()


def test_no_false_fail_when_stall_but_acceptable_metrics(demo_db, config_with_probation):
    """When stalled but PF/expectancy still acceptable, do not fail with fail_fast_stalled_poor_metrics."""
    config_with_probation.demo_probation.no_trade_stall_minutes = 1
    config_with_probation.demo_probation.fail_if_stalled_and_pf_below = 0.5
    config_with_probation.demo_probation.fail_if_stalled_and_negative_expectancy = True
    config_with_probation.demo_probation.min_closed_trades = 3
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
    for i in range(4):
        pnl = 1.0 if i % 2 == 0 else -0.3
        conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started + i * 100, "BTCUSDT", "Buy", 0.01, 100.0, f"o{i}", f"exit_{i}", pnl, cid),
        )
    conn.commit()
    db.close()
    import time
    time.sleep(1.1)
    status, lifecycle, reasons, metrics, failure_type = evaluate_probation(demo_db, config_with_probation, config_id=cid)
    assert failure_type != "fail_fast_stalled_poor_metrics"


def test_artifact_shows_failure_reason_type(demo_db, config_with_probation, tmp_path):
    """After fail-fast failure, artifact and record have failure_reason_type."""
    from src.demo_probation.artifacts import build_probation_status_payload, write_probation_status_artifact
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
        ["stalled_poor_metrics"], {"closed_trades": 5, "profit_factor": 0.8, "expectancy": -0.1},
        failure_reason_type="fail_fast_stalled_poor_metrics",
    )
    rec = get_probation_record(cid, demo_db)
    assert rec.get("failure_reason_type") == "fail_fast_stalled_poor_metrics"
    payload = build_probation_status_payload(
        cid, LIFECYCLE_DEMO_PROBATION_FAILED, PROBATION_STATUS_FAILED,
        {"closed_trades": 5}, ["stalled_poor_metrics"],
        rec.get("started_at_ts"), rec.get("updated_at_ts"), rec.get("ended_at_ts"), None, False,
        failure_reason_type="fail_fast_stalled_poor_metrics",
    )
    assert payload["failure_reason_type"] == "fail_fast_stalled_poor_metrics"
    path = write_probation_status_artifact(str(tmp_path), "demo", payload)
    assert path and path.exists()


def test_stop_demo_on_failure_true_stops_runtime(config_with_probation):
    """When stop_demo_on_failure=True, _stop_on_probation_failure sets running=False and returns True."""
    config_with_probation.demo_probation.stop_demo_on_failure = True
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot.running = True
    stopped = bot._stop_on_probation_failure()
    assert stopped is True
    assert bot.running is False


def test_stop_demo_on_failure_false_does_not_stop_runtime(config_with_probation):
    """When stop_demo_on_failure=False, _stop_on_probation_failure does not set running=False and returns False."""
    config_with_probation.demo_probation.stop_demo_on_failure = False
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot.running = True
    stopped = bot._stop_on_probation_failure()
    assert stopped is False
    assert bot.running is True


def test_probation_failure_log_message_when_stopping(config_with_probation):
    """When stop_demo_on_failure=True, stopping logs the operator-facing message."""
    from unittest.mock import patch
    config_with_probation.demo_probation.stop_demo_on_failure = True
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot.running = True
    with patch("src.main.log.warning") as mock_warn:
        bot._stop_on_probation_failure()
    mock_warn.assert_called_once()
    call_args = mock_warn.call_args[0][0]
    assert "Demo probation failed" in call_args
    assert "stopping Demo runtime" in call_args


def test_auto_reinit_true_sets_reinit_requested(config_with_probation):
    """When auto_reinit_after_failure=True, _stop_on_probation_failure sets _reinit_requested."""
    config_with_probation.demo_probation.stop_demo_on_failure = True
    config_with_probation.demo_probation.auto_reinit_after_failure = True
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot.running = True
    bot._stop_on_probation_failure()
    assert getattr(bot, "_reinit_requested", False) is True


def test_auto_reinit_false_does_not_set_reinit_requested(config_with_probation):
    """When auto_reinit_after_failure=False, _stop_on_probation_failure does not set _reinit_requested."""
    config_with_probation.demo_probation.stop_demo_on_failure = True
    config_with_probation.demo_probation.auto_reinit_after_failure = False
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot._reinit_requested = False
    bot._stop_on_probation_failure()
    assert getattr(bot, "_reinit_requested", False) is False


def test_run_returns_20_when_reinit_requested(config_with_probation):
    """run() returns EXIT_PROBATION_REINIT when _reinit_requested is True after loop exits (auto-reinit flow)."""
    from unittest.mock import Mock, patch
    from src.main import EXIT_PROBATION_REINIT
    config_with_probation.demo_probation.stop_demo_on_failure = True
    config_with_probation.demo_probation.auto_reinit_after_failure = True
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot._client = Mock()
    bot._client.stop_private_ws = Mock()
    bot._client.stop_public_ws = Mock()
    bot._db = Mock()
    bot._db.insert_equity = Mock()
    bot._db.close = Mock()
    bot._ws_shards = None
    with patch.object(bot, "_boot", return_value=True), patch.object(
        bot, "_score_and_enter_loop", side_effect=lambda: setattr(bot, "_reinit_requested", True)
    ):
        rc = bot.run()
    assert rc == EXIT_PROBATION_REINIT


def test_run_returns_none_when_no_reinit_requested(config_with_probation):
    """run() returns None when _reinit_requested is False (normal exit)."""
    from unittest.mock import Mock, patch
    from src.main import TradingBot
    from src.config.config import EnvSettings
    env = EnvSettings()
    env.bybit_env = "demo"
    bot = TradingBot(config_with_probation, env)
    bot._client = Mock()
    bot._client.stop_private_ws = Mock()
    bot._client.stop_public_ws = Mock()
    bot._db = Mock()
    bot._db.insert_equity = Mock()
    bot._db.close = Mock()
    bot._ws_shards = None
    with patch.object(bot, "_boot", return_value=True), patch.object(bot, "_score_and_enter_loop"):
        rc = bot.run()
    assert rc is None


def test_demo_probation_auto_reinit_enabled_cli(tmp_path):
    """CLI demo probation auto-reinit-enabled exits 0 when true, 1 when false."""
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    cfg_path = tmp_path / "config.yaml"
    import yaml
    with open(cfg_path, "w") as f:
        yaml.safe_dump({"demo_probation": {"enabled": True, "auto_reinit_after_failure": True}}, f)
    r = subprocess.run(
        [sys.executable, "run_bot.py", "demo", "probation", "auto-reinit-enabled", "--config", str(cfg_path)],
        cwd=str(repo),
        capture_output=True,
        timeout=10,
    )
    assert r.returncode == 0
    with open(cfg_path, "w") as f:
        yaml.safe_dump({"demo_probation": {"enabled": True, "auto_reinit_after_failure": False}}, f)
    r2 = subprocess.run(
        [sys.executable, "run_bot.py", "demo", "probation", "auto-reinit-enabled", "--config", str(cfg_path)],
        cwd=str(repo),
        capture_output=True,
        timeout=10,
    )
    assert r2.returncode == 1


def test_start_demo_script_has_reinit_loop_and_exit_code():
    """start_demo_research.sh documents exit code 20 and contains re-init loop logic."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "start_demo_research.sh"
    if not script.exists():
        return
    text = script.read_text()
    assert "EXIT_PROBATION_REINIT=20" in text or "20" in text
    assert "auto_reinit_after_failure" in text or "auto-reinit-enabled" in text
    assert "re-initializing" in text or "re-initializing" in text.lower()
    assert "Demo runtime exited due to probation failure" in text


def test_run_probation_fail_fast_check_writes_artifact_before_returning_true(demo_db, config_with_probation, tmp_path):
    """When probation fails, run_probation_fail_fast_check writes artifact and state before returning True."""
    config_with_probation.artifacts_root = str(tmp_path)
    config_with_probation.instance_name = "demo"
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
    db = Database(demo_db)
    conn = db._get_conn()
    conn.execute("UPDATE demo_probation SET started_at_ts = ? WHERE config_id = ?", (1000000, cid))
    conn.commit()
    conn.execute("INSERT INTO kill_switch_events (ts, reason) VALUES (?, ?)", (1001000, "test"))
    conn.commit()
    db.close()
    from src.demo_probation import run_probation_fail_fast_check
    result = run_probation_fail_fast_check(demo_db, config_with_probation)
    assert result is True
    rec = get_probation_record(cid, demo_db)
    assert rec["lifecycle_state"] == LIFECYCLE_DEMO_PROBATION_FAILED
    assert rec.get("failure_reason_type") == "fail_fast_kill_switch"
    artifact_path = tmp_path / "demo" / "probation" / "demo_probation_status.json"
    assert artifact_path.exists()
