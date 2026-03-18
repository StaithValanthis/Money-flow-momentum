"""Protection-aware warm-start search/reporting tests (Demo-only)."""

from pathlib import Path

from src.config.config import Config, EnvSettings, WarmStartConfig
from src.optimizer.parameter_space import get_bounded_space
from src.warm_start.candidate_search import run_warm_start_candidate_search
from src.warm_start import runner as warm_start_runner


def test_parameter_space_includes_time_stop_and_trailing_when_prioritize_enabled() -> None:
    space = get_bounded_space(
        stage4=True,
        stage5=True,
        prioritize_protection_search=True,
        protection_search_bias="wider_stops",
    )
    assert "stop_tp.time_stop_bars" in space.bounds
    assert "stop_tp.trailing_stop_atr_multiple" in space.bounds
    assert "stop_tp.atr_multiplier_sl" in space.bounds

    # Without prioritize, time_stop_bars/trailing_stop are not part of the bounded space.
    space2 = get_bounded_space(stage4=True, stage5=True)
    assert "stop_tp.time_stop_bars" not in space2.bounds
    assert "stop_tp.trailing_stop_atr_multiple" not in space2.bounds


def test_candidate_search_adds_protection_settings_and_diagnostics(monkeypatch) -> None:
    # Minimal baseline config stub.
    baseline = Config()
    baseline.warm_start = WarmStartConfig(
        enabled=True,
        prioritize_protection_search=True,
        protection_search_bias="balanced",
    )

    candles_by_symbol = {"BTCUSDT": [{"start_ts": 1000, "open": 1, "high": 1, "low": 1, "close": 1}] * 5}

    # Avoid real backtest: return metrics that fail guardrails.
    dummy_metrics = {
        "trade_count": 0,
        "total_pnl": -1.0,
        "return_pct": -1.0,
        "max_drawdown": 0.0,
        "expectancy": -0.1,
        "win_rate": 0.0,
        "profit_factor": 0.8,
        "payoff_ratio": 0.7,
        "sharpe_like": -0.1,
        "median_trade_duration_sec": 30.0,
        "stop_out_rate": 0.6,
        "max_consecutive_losses": 4,
        "exit_reason_counts": {},
        "tp1_hit_rate": 0.0,
        "tp2_hit_rate": 0.0,
        # fields used by candidate_search / objective
        "avg_loss": -1.0,
    }

    dummy_meta = {
        "trade_count": 0,
        "trade_count_requested": 0,
        "engine": "parameter_aware_protection_backtest",
        "exit_reason_counts": {},
    }

    monkeypatch.setattr(
        "src.warm_start.candidate_search.build_config_from_params",
        lambda parent_config, params: baseline,
    )
    monkeypatch.setattr(
        "src.warm_start.candidate_search.run_backtest_on_candles",
        lambda config, candles, fee_bps, slippage_bps: ([], dummy_metrics, dummy_meta),
    )

    best, results, meta = run_warm_start_candidate_search(
        baseline,
        candles_by_symbol=candles_by_symbol,
        n_samples=3,
        min_trades_guardrail=5,
        require_profitable=False,
        max_runtime_seconds=None,
        start_time=0.0,
    )

    # All candidates should be rejected by guardrails due to trade_count=0.
    assert best is None
    assert len(results) == 3
    for r in results:
        assert "protection_settings" in r
        assert "protection_diagnostic" in r
        # At minimum in prioritized mode: time_stop_bars should be sampled and present.
        assert "time_stop_bars" in r["protection_settings"]
        assert isinstance(r["protection_settings"]["time_stop_bars"], int)

    assert "top_rejected_candidates" in meta
    assert meta["top_rejected_candidates"], "Expected top rejected candidates slice to be populated"
    for tr in meta["top_rejected_candidates"]:
        assert "protection_settings" in tr
        assert "protection_diagnostic" in tr
        assert "time_stop_bars" in tr["protection_settings"]
        assert isinstance(tr["protection_settings"]["time_stop_bars"], int)


def test_warm_start_artifact_includes_protection_fields(tmp_path: Path) -> None:
    cfg = Config()
    cfg.operating_mode = "demo_research"
    cfg.artifacts_root = str(tmp_path / "artifacts")
    cfg.instance_name = "demo"

    result = {
        "success": False,
        "top_rejected_candidates": [
            {
                "config_id": "x1",
                "protection_settings": {
                    "atr_multiplier_sl": 2.0,
                    "tp1_r_multiple": 1.0,
                    "tp2_r_multiple": 2.0,
                    "time_stop_bars": 90,
                },
                "protection_diagnostic": "likely_too_tight_stops",
                "oos_metrics": {"stop_out_rate": 0.6, "profit_factor": 0.9},
                "reason_codes": ["low_oos_trade_count"],
                "objective_score": -1.0,
            }
        ],
        "best_candidate_protection_settings": {
            "atr_multiplier_sl": 1.5,
            "tp1_r_multiple": 1.0,
            "tp2_r_multiple": 2.0,
            "time_stop_bars": 60,
        },
        "best_candidate_protection_diagnostic": "mixed_or_unclear",
    }

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Access internal helper (it’s used to write warm_start_report.json).
    warm_start_runner._write_warm_start_artifact(artifact_dir, result, cfg)  # type: ignore[attr-defined]

    report_path = Path(cfg.artifacts_root) / "warm_start" / "warm_start_report.json"
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "best_candidate_protection_settings" in content
    assert "top_rejected_candidates" in content
    assert "protection_diagnostic" in content

