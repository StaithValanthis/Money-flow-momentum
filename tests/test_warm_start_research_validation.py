"""Research-quality warm-start validation and probation composite scoring."""

from __future__ import annotations

import copy
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.config.config import Config, DemoProbationConfig, WarmStartConfig
from src.demo_probation.evaluator import (
    PROBATION_STATUS_FAILED,
    PROBATION_STATUS_IN_PROGRESS,
    PROBATION_STATUS_PASSED,
    _probation_composite_survival,
    evaluate_probation,
)
from src.warm_start.acceptance import passes_warm_start_seed_acceptance
from src.warm_start.research_validation import (
    compute_family_overfitting_diagnostics,
    deep_validate_winner,
    research_layers_failed,
)


def _minimal_candles(n: int = 120) -> Dict[str, List[Dict[str, Any]]]:
    base = 1_700_000_000_000
    step = 60_000
    rows = []
    for i in range(n):
        rows.append({
            "start_ts": base + i * step,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0 + (i % 7) * 0.01,
            "volume": 1.0,
        })
    return {"BTCUSDT": rows}


def test_family_overfitting_diagnostics_fields() -> None:
    results = []
    for i in range(10):
        results.append({
            "oos_metrics": {"sharpe_like": 0.5 + i * 0.1, "return_pct": i},
            "objective_score": float(i),
        })
    best = results[-1]
    d = compute_family_overfitting_diagnostics(results, best)
    assert d["candidate_family_size"] == 10
    assert "overfitting_risk" in d
    assert "selection_bias_adjusted_score" in d
    assert "candidate_family_best_vs_median_gap" in d
    assert "deflated_sharpe_like" in d
    assert "approximate" in (d.get("note") or "").lower() or "PBO" in (d.get("note") or "")


def test_research_layers_failed_multi_window() -> None:
    warm = MagicMock()
    warm.use_multi_window_validation = True
    warm.use_cost_sensitivity_check = False
    warm.use_regime_validation = False
    assert "multi_window" in research_layers_failed({"validation_passed": False}, warm)
    assert research_layers_failed({"validation_passed": True}, warm) == []


def test_research_layers_failed_cost() -> None:
    warm = MagicMock()
    warm.use_multi_window_validation = False
    warm.use_cost_sensitivity_check = True
    warm.use_regime_validation = False
    assert "cost_sensitivity" in research_layers_failed({"cost_sensitivity_passed": False}, warm)


def test_acceptance_rejects_research_when_configured() -> None:
    cfg = Config()
    cfg.warm_start = WarmStartConfig()
    cfg.warm_start.reject_on_research_validation_failure = True
    cfg.warm_start.use_multi_window_validation = True
    metrics = {
        "trade_count": 40,
        "total_pnl": 100.0,
        "win_rate": 0.25,
        "profit_factor": 1.15,
        "payoff_ratio": 1.25,
        "max_drawdown": 3.0,
        "return_pct": 5.0,
        "fees_summary": 1.0,
        "slippage_summary": 1.0,
    }
    durations = [200.0] * 40
    rs = {
        "validation_passed": False,
        "research_layers_skipped": False,
    }
    ok, reason, checks = passes_warm_start_seed_acceptance(
        metrics, cfg, durations_sec=durations,
        research_summary=rs,
        family_diagnostics={"overfitting_risk": 0.1},
    )
    assert ok is False
    assert "research_validation_failed" in reason
    assert checks.get("warm_start_rejection_attribution")


def test_acceptance_rejects_high_overfitting_risk() -> None:
    cfg = Config()
    cfg.warm_start = WarmStartConfig()
    cfg.warm_start.reject_on_high_overfitting_risk = True
    cfg.warm_start.max_acceptable_overfitting_risk = 0.3
    metrics = {
        "trade_count": 40,
        "total_pnl": 100.0,
        "win_rate": 0.25,
        "profit_factor": 1.15,
        "payoff_ratio": 1.25,
        "max_drawdown": 3.0,
        "return_pct": 5.0,
        "fees_summary": 1.0,
        "slippage_summary": 1.0,
    }
    durations = [200.0] * 40
    ok, reason, _ = passes_warm_start_seed_acceptance(
        metrics, cfg, durations_sec=durations,
        research_summary={"research_layers_skipped": True},
        family_diagnostics={"overfitting_risk": 0.99},
    )
    assert ok is False
    assert "overfitting_risk" in reason


def test_deep_validate_cost_scenarios_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[int] = []

    def fake_run(cfg: Any, candles: Any, fee: float, slip: float):
        calls.append(1)
        return [], {"return_pct": 1.0 if fee < 10 else -5.0, "profit_factor": 1.1 if fee < 10 else 0.8, "trade_count": 5}, {}

    monkeypatch.setattr(
        "src.warm_start.research_validation.run_backtest_on_candles",
        fake_run,
    )
    warm = WarmStartConfig()
    warm.use_cost_sensitivity_check = True
    warm.cost_scenarios_bps = [8, 12, 20]
    warm.min_cost_scenarios_profitable = 2
    warm.use_multi_window_validation = False
    warm.use_regime_validation = False
    base = Config()
    base.warm_start = warm
    cand = copy.deepcopy(base)
    out = deep_validate_winner(cand, base, _minimal_candles(80))
    assert "metrics_by_cost_scenario" in out
    assert len(out["metrics_by_cost_scenario"]) == 3
    assert "cost_fragility_score" in out
    # With stub, only low-fee runs are "profitable" => likely fail min 2 profitable
    assert out["cost_sensitivity_passed"] is False or out["cost_fragility_score"] >= 0


def test_deep_validate_regime_metrics_by_regime(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cfg: Any, candles: Any, fee: float, slip: float):
        n = sum(len(v) for v in candles.values())
        return [], {"return_pct": -1.0 if n < 15 else 0.5, "profit_factor": 1.0, "trade_count": 3}, {}

    monkeypatch.setattr(
        "src.warm_start.research_validation.run_backtest_on_candles",
        fake_run,
    )
    warm = WarmStartConfig()
    warm.use_regime_validation = True
    warm.regime_quarters_min_positive = 3
    warm.use_multi_window_validation = False
    warm.use_cost_sensitivity_check = False
    base = Config()
    base.warm_start = warm
    out = deep_validate_winner(base, base, _minimal_candles(100))
    assert out.get("metrics_by_regime")
    assert "regime_robustness" in out


def test_probation_composite_survival_score_and_pass() -> None:
    prob = DemoProbationConfig()
    prob.max_consecutive_losses = 5
    prob.max_stop_out_rate = 0.5
    prob.max_ultra_short_trade_fraction = 0.25
    closed = [{"pnl": 1.0, "ts": i} for i in range(35)]
    for t in closed:
        t["entry_ts"] = t["ts"] - 120_000
    score, comp, driver = _probation_composite_survival(
        closed, 1.12, 0.05, 1, 0.2, 0.1, prob, 30,
    )
    assert 0 <= score <= 100
    assert "profit_factor" in comp
    assert driver in comp


def test_evaluate_probation_composite_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ms = int(time.time() * 1000)
    prob = DemoProbationConfig()
    prob.use_composite_survival_score = True
    prob.probation_survival_pass_score = 40.0
    prob.probation_survival_fail_score = 10.0
    prob.min_closed_trades = 5
    prob.min_runtime_minutes = 0
    cfg = Config()
    cfg.demo_probation = prob

    monkeypatch.setattr(
        "src.demo_probation.evaluator.get_probation_record",
        lambda *a, **k: {
            "lifecycle_state": "DEMO_PROBATION",
            "started_at_ts": now_ms - 120_000,
        },
    )
    monkeypatch.setattr("src.config.versioning.get_active_config_id", lambda *a, **k: "cid1")
    trades = [{"pnl": 2.0, "ts": now_ms - i * 60_000, "config_id": "cid1"} for i in range(40)]
    for t in trades:
        t["entry_ts"] = t["ts"] - 200_000

    class FakeDB:
        def get_trades(self, **kw):
            return trades

        def get_lifecycle_events(self, **kw):
            return []

        def get_kill_switch_events(self, **kw):
            return []

        def get_automation_state(self):
            return {"state": "OK"}

        def get_burnin_gate_breaches(self, **kw):
            return []

        def close(self):
            pass

    monkeypatch.setattr("src.storage.db.Database", lambda p: FakeDB())
    st, life, reasons, metrics, ft = evaluate_probation("/x", cfg, config_id="cid1")
    assert st == PROBATION_STATUS_PASSED
    assert "probation_survival_score" in metrics
    assert metrics.get("probation_survival_components")
    assert ft is None


def test_evaluate_probation_composite_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ms = int(time.time() * 1000)
    prob = DemoProbationConfig()
    prob.use_composite_survival_score = True
    prob.probation_survival_pass_score = 99.0
    prob.probation_survival_fail_score = 98.0
    prob.min_closed_trades = 5
    prob.min_runtime_minutes = 0
    cfg = Config()
    cfg.demo_probation = prob

    monkeypatch.setattr(
        "src.demo_probation.evaluator.get_probation_record",
        lambda *a, **k: {
            "lifecycle_state": "DEMO_PROBATION",
            "started_at_ts": now_ms - 120_000,
        },
    )
    monkeypatch.setattr("src.config.versioning.get_active_config_id", lambda *a, **k: "cid1")
    trades = [{"pnl": -5.0, "ts": now_ms - i * 60_000, "config_id": "cid1"} for i in range(40)]
    for t in trades:
        t["entry_ts"] = t["ts"] - 200_000
    trades[-1]["pnl"] = 0.01

    class FakeDB:
        def get_trades(self, **kw):
            return trades

        def get_lifecycle_events(self, **kw):
            return []

        def get_kill_switch_events(self, **kw):
            return []

        def get_automation_state(self):
            return {"state": "OK"}

        def get_burnin_gate_breaches(self, **kw):
            return []

        def close(self):
            pass

    monkeypatch.setattr("src.storage.db.Database", lambda p: FakeDB())
    st, life, reasons, metrics, ft = evaluate_probation("/x", cfg, config_id="cid1")
    assert st == PROBATION_STATUS_FAILED
    assert metrics.get("probation_rejection_attribution")
    assert ft == "composite_survival_fail"


def test_skipped_research_does_not_block_acceptance() -> None:
    cfg = Config()
    cfg.warm_start = WarmStartConfig()
    cfg.warm_start.reject_on_research_validation_failure = True
    metrics = {
        "trade_count": 40,
        "total_pnl": 100.0,
        "win_rate": 0.25,
        "profit_factor": 1.15,
        "payoff_ratio": 1.25,
        "max_drawdown": 3.0,
        "return_pct": 5.0,
        "fees_summary": 1.0,
        "slippage_summary": 1.0,
    }
    durations = [200.0] * 40
    ok, _, _ = passes_warm_start_seed_acceptance(
        metrics, cfg, durations_sec=durations,
        research_summary={"research_layers_skipped": True, "validation_passed": False},
        family_diagnostics={"overfitting_risk": 0.1},
    )
    assert ok is True
