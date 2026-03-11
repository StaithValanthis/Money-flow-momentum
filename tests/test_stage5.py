"""Stage 5 tests: risk budget, allocator, exposure, strategy registry, fill model, health, artifacts, Stage 5.1 hardening."""

import sys
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from src.config.config import Config, RiskConfig, PortfolioExposureConfig
from src.portfolio.risk_budget import (
    RiskBudgetState,
    check_total_risk_budget,
    check_long_risk_budget,
    check_cluster_risk_budget,
    build_budget_state,
)
from src.risk.risk_engine import RiskEngine, PositionSizingResult
from src.portfolio.allocator import (
    allocate_risk,
    allocate_candidate_set,
    AllocationDecision,
    CandidateForAllocation,
)
from src.portfolio.exposure_controls import same_direction_concentration_penalty
from src.strategies.registry import get_strategy, list_strategies
from src.strategies.base import BaseStrategy
from src.research.fill_model import FillModelConfig, fill_result, apply_slippage
from src.monitoring.health import HealthSnapshot
from src.monitoring.heartbeat import write_heartbeat, read_heartbeat
from src.monitoring.alerts import AlertRouter
from src.storage.artifacts import artifacts_root, ensure_artifact_dirs
from src.evaluation.metrics import compute_stage5_portfolio_metrics
from src.optimizer.parameter_space import get_bounded_space


def test_risk_budget_total():
    state = RiskBudgetState(total_risk_usdt=100, equity_usdt=10_000)
    config = RiskConfig(max_total_risk_pct=2.0)
    r = check_total_risk_budget(state, config, additional_risk_usdt=150)
    assert r.allowed is False
    r2 = check_total_risk_budget(state, config, additional_risk_usdt=50)
    assert r2.allowed is True


def test_risk_budget_cluster():
    state = RiskBudgetState(equity_usdt=10_000, cluster_risk_usdt={0: 50})
    config = RiskConfig(max_cluster_risk_pct=1.0)
    r = check_cluster_risk_budget(state, config, 0, additional_risk_usdt=60)
    assert r.allowed is False


def test_build_budget_state():
    positions = [
        ("A", "Buy", 1.0, 100.0),
        ("B", "Sell", 0.5, 50.0),
    ]
    state = build_budget_state(10_000, positions, {"A": 0, "B": 0})
    assert state.total_risk_usdt == 100 + 25
    assert state.long_risk_usdt == 100
    assert state.short_risk_usdt == 25
    assert state.cluster_risk_usdt.get(0) == 125


def test_allocator_blocks_on_budget():
    config = RiskConfig(max_total_risk_pct=0.5)
    state = RiskBudgetState(total_risk_usdt=40, equity_usdt=10_000)
    base = PositionSizingResult(qty=1, notional_usdt=1000, risk_usdt=60, stop_price=99, r_multiple=0.01)
    alloc = allocate_risk(base, "X", "Buy", 1.5, None, state, config, "equal_risk", 60)
    assert alloc.reject_reason is not None


def test_exposure_concentration_penalty():
    penalty = same_direction_concentration_penalty(2, 0, "Buy", PortfolioExposureConfig(same_direction_concentration_penalty_pct=10))
    assert penalty >= 0


def test_strategy_registry():
    assert "flow_impulse" in list_strategies()
    config = Config()
    s = get_strategy("flow_impulse", config)
    assert s is not None
    assert s.name == "flow_impulse"
    assert get_strategy("unknown", config) is None


def test_fill_model():
    cfg = FillModelConfig(slippage_bps=20)
    res = fill_result("Buy", 100.0, 1.0, cfg)
    assert res.fill_price > 100
    assert res.slippage_cost_usdt > 0
    p, c = apply_slippage("Sell", 100.0, 1.0, 20)
    assert p < 100


def test_health_snapshot():
    h = HealthSnapshot()
    h.report_ok("test_loop")
    d = h.to_dict()
    assert "loops" in d
    assert d["loops"]["test_loop"]["status"] == "ok"
    h.report_fail("other", "error")
    assert h.get_loop("other").status == "fail"


def test_alert_router_no_webhook():
    r = AlertRouter(webhook_url=None, enabled=True)
    r.send("warn", "Test", "message")  # should not raise


def test_artifacts_dirs():
    ensure_artifact_dirs()
    root = artifacts_root()
    assert root.exists()
    assert (root / "evaluations").exists()


def test_stage5_portfolio_metrics():
    entry = [{"reason": "rejected:stage5:total_risk_budget:100>200"}, {"reason": "order_placed"}]
    m = compute_stage5_portfolio_metrics(entry, [])
    assert m["budget_block_count"] >= 1
    assert "stage5_rejection_counts" in m


def test_heartbeat_write_and_read():
    """Heartbeat file is written by write_heartbeat and contains loop timestamps."""
    health = HealthSnapshot()
    health.register("score_entry")
    health.report_ok("score_entry")
    health.set_meta("config_id", "test-1")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "heartbeat.json"
        write_heartbeat(health, path)
        assert path.exists()
        data = read_heartbeat(path)
        assert data and "ts" in data and "loops" in data
        assert data["loops"]["score_entry"]["status"] == "ok"
        assert data.get("meta", {}).get("config_id") == "test-1"


def test_health_command_stale_vs_fresh(tmp_path):
    """Health command exits 1 when heartbeat is stale (stale-sec)."""
    root = Path(__file__).resolve().parents[1]
    hb = tmp_path / "heartbeat.json"
    health = HealthSnapshot()
    health.report_ok("score_entry")
    write_heartbeat(health, hb)
    r = subprocess.run(
        [sys.executable, "run_bot.py", "health", "--heartbeat", str(hb), "--stale-sec", "10"],
        capture_output=True, text=True, cwd=root,
    )
    assert r.returncode == 0, r.stderr
    import json
    data = read_heartbeat(hb)
    if data and "loops" in data:
        for k in data["loops"]:
            data["loops"][k]["last_ok_ts"] = time.time() - 20
        data["ts"] = time.time() - 20
        with open(hb, "w") as f:
            json.dump(data, f)
    r2 = subprocess.run(
        [sys.executable, "run_bot.py", "health", "--heartbeat", str(hb), "--stale-sec", "10"],
        capture_output=True, text=True, cwd=root,
    )
    assert r2.returncode == 1


def test_health_command_degradation_monitor_slow_cadence_ok(tmp_path):
    """Health passes when degradation_monitor is older than default 300s but within its 900s threshold."""
    root = Path(__file__).resolve().parents[1]
    hb = tmp_path / "heartbeat.json"
    health = HealthSnapshot()
    health.report_ok("score_entry")
    health.report_ok("public_ws")
    health.report_ok("degradation_monitor")
    write_heartbeat(health, hb)
    # Make degradation_monitor 400s old; others and file ts fresh
    import json
    data = read_heartbeat(hb)
    assert data and "loops" in data
    now = time.time()
    data["ts"] = now - 10
    data["loops"]["score_entry"]["last_ok_ts"] = now - 10
    data["loops"]["public_ws"]["last_ok_ts"] = now - 10
    data["loops"]["degradation_monitor"]["last_ok_ts"] = now - 400
    with open(hb, "w") as f:
        json.dump(data, f)
    r = subprocess.run(
        [sys.executable, "run_bot.py", "health", "--heartbeat", str(hb), "--stale-sec", "300"],
        capture_output=True, text=True, cwd=root,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    # degradation_monitor >900s => health should fail
    data["loops"]["degradation_monitor"]["last_ok_ts"] = now - 1000
    with open(hb, "w") as f:
        json.dump(data, f)
    r2 = subprocess.run(
        [sys.executable, "run_bot.py", "health", "--heartbeat", str(hb), "--stale-sec", "300"],
        capture_output=True, text=True, cwd=root,
    )
    assert r2.returncode == 1


def test_allocator_candidate_set_distributes():
    """Allocator distributes across candidate set."""
    config = RiskConfig(max_total_risk_pct=1.0, max_concurrent_positions=5)
    state = RiskBudgetState(equity_usdt=10_000, total_risk_usdt=0, long_risk_usdt=0, short_risk_usdt=0)
    base1 = PositionSizingResult(qty=10, notional_usdt=1000, risk_usdt=80, stop_price=99, r_multiple=0.01)
    base2 = PositionSizingResult(qty=10, notional_usdt=1000, risk_usdt=80, stop_price=99, r_multiple=0.01)
    c1 = CandidateForAllocation("A", "Buy", 2.0, base1, None, 100, 99, 1.0, 0.1, 0.01)
    c2 = CandidateForAllocation("B", "Buy", 0.5, base2, None, 100, 99, 1.0, 0.1, 0.01)
    decisions = allocate_candidate_set([c1, c2], state, config, "equal_risk")
    assert len(decisions) == 2
    first_dec = decisions[0][1]
    assert first_dec.reject_reason is None
    assert first_dec.original_risk_usdt == 80
    assert first_dec.qty > 0 or decisions[1][1].qty > 0 or decisions[1][1].reject_reason is not None


def test_allocator_single_candidate_backward_compatible():
    """Single-candidate path: one candidate gets allocation."""
    config = RiskConfig(max_total_risk_pct=5.0, max_concurrent_positions=5)
    state = RiskBudgetState(equity_usdt=10_000, total_risk_usdt=0, long_risk_usdt=0, short_risk_usdt=0)
    base = PositionSizingResult(qty=1.0, notional_usdt=100, risk_usdt=50, stop_price=99, r_multiple=0.01)
    c = CandidateForAllocation("X", "Buy", 1.5, base, None, 100, 99, 1.0, 0.01, 0.01)
    decisions = allocate_candidate_set([c], state, config, "equal_risk")
    assert len(decisions) == 1 and decisions[0][1].reject_reason is None and decisions[0][1].qty > 0
    assert decisions[0][1].original_qty == 1.0


def test_optimizer_parameter_space_includes_stage5():
    """Bounded parameter space includes Stage 5 params when stage5=True."""
    space = get_bounded_space(stage4=True, stage5=True)
    assert "risk.allocation_method" in space.bounds or "risk.allocation_method" in space.discrete
    assert "risk.max_cluster_risk_pct" in space.bounds
    assert "portfolio_exposure.max_gross_exposure_per_cluster_pct" in space.bounds
    assert "equal_risk" in space.discrete.get("risk.allocation_method", [])


def test_status_report_no_heartbeat_graceful():
    """Status and report run without crash when heartbeat file is missing."""
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "run_bot.py", "status"], capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stderr
    r2 = subprocess.run([sys.executable, "run_bot.py", "report"], capture_output=True, text=True, cwd=root)
    assert r2.returncode == 0, r2.stderr
    assert "heartbeat" in r.stdout.lower() or "Heartbeat" in r.stdout or "heartbeat" in r2.stdout.lower() or "No heartbeat" in r2.stdout


def test_evaluation_allocation_counts():
    """Stage 5 portfolio metrics include resized_by_allocation and allocation_method_usage."""
    entry_decisions = [
        {"reason": "order_placed:resized"},
        {"reason": "order_placed:capped_score_weighted:resized"},
        {"reason": "rejected:stage5:total_risk_budget:no_room"},
    ]
    m = compute_stage5_portfolio_metrics(entry_decisions, [])
    assert "resized_by_allocation_count" in m
    assert m["resized_by_allocation_count"] == 2
    assert "allocation_method_usage" in m
    assert m["allocation_method_usage"].get("capped_score_weighted") == 1
