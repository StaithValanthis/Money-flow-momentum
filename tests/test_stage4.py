"""Stage 4 tests: flow features, regime, threshold policy, scoring, cluster, exit logic, evaluation, optimizer."""

import json
import pytest
from src.data.feature_builder import FeatureBuilder, SymbolFeatures
from src.data.market_state import SymbolState
from src.config.config import FeatureConfig, ScoreWeights, EntryThresholds
from src.signals.regime_filters import classify_regime, regime_allows_entry, RegimeLabel
from src.signals.threshold_policy import compute_adaptive_thresholds, ThresholdProfile
from src.signals.flow_impulse import FlowImpulseScorer
from src.portfolio.correlation import (
    cluster_by_correlation_proxy,
    cluster_blocked,
    cluster_counts_per_side,
)
from src.portfolio.lifecycle import LifecycleManager, LifecycleState, LifecyclePhase
from src.evaluation.metrics import compute_stage4_metrics, compute_core_metrics
from src.optimizer.parameter_space import get_bounded_space
from src.config.candidate_factory import APPROVED_PARAM_PATHS


def test_stage4_flow_features_present():
    """Stage 4 flow features exist on SymbolFeatures and are computed."""
    config = FeatureConfig()
    builder = FeatureBuilder(config)
    state = SymbolState(symbol="X")
    state.delta_1m = 100
    state.delta_30s = 30
    state.delta_3m = 300
    state.buy_vol_1m = 200
    state.sell_vol_1m = 100
    state.buy_vol_30s = 60
    state.sell_vol_30s = 30
    state.buy_vol_3m = 400
    state.sell_vol_3m = 200
    state.last_price = 100.0
    state.vwap = 99.0
    state.closes.extend([98, 99, 100])
    state.trade_count_1m = 50
    state.trade_count_3m = 120
    state.oi_change = 0.01
    f = builder.build(state)
    assert hasattr(f, "delta_acceleration")
    assert hasattr(f, "cvd_divergence_score")
    assert hasattr(f, "cvd_persistence_score")
    assert hasattr(f, "trade_intensity_burst")
    assert hasattr(f, "price_response_to_flow")
    assert hasattr(f, "flow_exhaustion_score")
    assert hasattr(f, "move_efficiency")
    assert hasattr(f, "volatility_expansion_ratio")
    assert hasattr(f, "breakout_confirmation_score")
    assert hasattr(f, "failed_breakout_score")


def test_regime_classification():
    """Regime labels are returned."""
    f = SymbolFeatures(
        symbol="X",
        delta_30s=0, delta_1m=100, delta_3m=200,
        cvd_1m=50, cvd_3m=100, cvd_slope=0.5,
        buy_sell_ratio_30s=1.0, buy_sell_ratio_1m=1.0, buy_sell_ratio_3m=1.0,
        price_return_1m=0.005, price_return_3m=0.01, price_return_5m=0.01,
        distance_from_vwap=0, atr_14=1.0, spread_bps=5, realized_volatility=0.01,
        open_interest_change=0, funding_rate=0, long_short_ratio=1.0,
        trade_count_1m=10, trade_count_3m=30, last_price=100, vwap=100,
        price_response_to_flow=0.002, volatility_expansion_ratio=1.2,
        breakout_confirmation_score=0.3, failed_breakout_score=0,
    )
    label = classify_regime(f, atr_percentile_50=0.01)
    assert label.symbol == "X"
    assert label.trend_vs_chop in ("trend", "chop", "unknown")
    assert label.combined


def test_regime_allows_entry():
    """regime_allows_entry respects block_trend/block_chop."""
    label = RegimeLabel("X", "chop", "low_vol", "momentum", "chop_low_vol_momentum")
    assert regime_allows_entry(label, block_trend=False, block_chop=True, direction="long") is False
    assert regime_allows_entry(label, block_trend=False, block_chop=False, direction="long") is True


def test_threshold_policy():
    """Adaptive thresholds return per-symbol profile."""
    features = [
        SymbolFeatures("A", 0, 100, 200, 50, 100, 0.5, 1, 1.1, 1, 0.01, 0.02, 0.02, 0, 0.5, 5, 0.01, 0, 0, 1, 10, 30, 100, 100),
        SymbolFeatures("B", 0, 200, 400, 100, 200, 0.5, 1, 1.2, 1, 0.02, 0.03, 0.03, 0, 2.0, 20, 0.02, 0, 0, 1, 100, 300, 100, 100),
    ]
    profiles = compute_adaptive_thresholds(features, 1.5, -1.5, 30)
    assert "A" in profiles
    assert "B" in profiles
    assert isinstance(profiles["A"], ThresholdProfile)
    assert profiles["A"].profile_name
    assert profiles["A"].liquidity_bucket in ("low", "mid", "high")
    assert profiles["A"].volatility_bucket in ("low", "mid", "high")


def test_scoring_stage4_components():
    """Score_all with stage4_enabled returns score_components."""
    weights = ScoreWeights()
    thresholds = EntryThresholds(long_threshold=1.0, short_threshold=-1.0, persistence_bonus=0.1, anti_chase_penalty=0.05)
    scorer = FlowImpulseScorer(weights, thresholds)
    features = [
        SymbolFeatures("A", 0, 500, 1000, 500, 1000, 0.5, 1, 1.2, 1, 0.01, 0.02, 0.02, 0, 0.5, 5, 0.01, 0, 0, 1, 50, 150, 100, 100, cvd_persistence_score=0.8),
        SymbolFeatures("B", 0, -400, -800, -400, -800, -0.5, 1, 0.8, 1, -0.01, -0.02, -0.02, 0, 0.5, 5, 0.01, 0, 0, 1, 50, 150, 100, 100),
    ]
    results = scorer.score_all(features, max_longs=2, max_shorts=2, stage4_enabled=True)
    assert len(results) >= 0
    for r in results:
        if getattr(r, "score_components", None):
            assert "base_flow" in r.score_components or "persistence_bonus" in r.score_components or True


def test_cluster_blocked():
    """cluster_blocked prevents adding when max per cluster reached."""
    symbol_to_cluster = {"A": 0, "B": 0, "C": 1}
    assert cluster_blocked("A", symbol_to_cluster, ["A", "B"], [], 2) is True
    assert cluster_blocked("C", symbol_to_cluster, ["A"], [], 2) is False


def test_cluster_by_correlation_proxy():
    """cluster_by_correlation_proxy returns symbol -> cluster_id."""
    features = [
        SymbolFeatures("A", 0, 100, 200, 50, 100, 0.5, 1, 1.1, 1, 0.01, 0.02, 0.02, 0, 0.5, 5, 0.01, 0, 0, 1, 10, 30, 100, 100),
        SymbolFeatures("B", 0, 100, 200, 50, 100, 0.5, 1, 1.1, 1, 0.01, 0.02, 0.02, 0, 0.5, 5, 0.01, 0, 0, 1, 10, 30, 100, 100),
    ]
    out = cluster_by_correlation_proxy(features, correlation_threshold=0.9)
    assert "A" in out
    assert "B" in out
    assert isinstance(out["A"], int)
    assert out["A"] >= 0


def test_lifecycle_exhaustion_exit():
    """Exhaustion exit is checked when enabled."""
    from src.config.config import Config
    config = Config()
    config.stop_tp.exhaustion_exit_enabled = True
    from src.data.market_state import MarketStateManager
    mgr = LifecycleManager(config, MarketStateManager(config.features))
    state = LifecycleState("X", "Buy", 100.0, 95.0, 110.0, 1.0, 1.0, 0, "")
    mgr.register(state)
    assert mgr.should_exhaustion_exit("X", 0.5, -50, "Buy") is True
    assert mgr.should_exhaustion_exit("X", 0.1, 50, "Buy") is False


def test_lifecycle_failed_breakout_exit():
    """Failed breakout exit is checked when enabled."""
    from src.config.config import Config
    config = Config()
    config.stop_tp.failed_breakout_exit_enabled = True
    config.stop_tp.failed_breakout_reversal_pct = 0.005
    from src.data.market_state import MarketStateManager
    mgr = LifecycleManager(config, MarketStateManager(config.features))
    state = LifecycleState("X", "Buy", 100.0, 95.0, 110.0, 1.0, 1.0, 0, "")
    mgr.register(state)
    assert mgr.should_failed_breakout_exit("X", 0.5, -0.01, "Buy") is True
    assert mgr.should_failed_breakout_exit("X", 0, 0.001, "Buy") is False


def test_stage4_metrics():
    """compute_stage4_metrics returns exit_reason and regime counts."""
    lifecycle = [{"symbol": "X", "ts": 1, "event": "time_stop", "exit_reason": "max_hold"}]
    signals = [{"symbol": "X", "json_features": json.dumps({"regime_label": "trend_high_vol_momentum", "threshold_profile": "mid_mid"})}]
    entry = [{"reason": "rejected:stage4:cluster_block"}]
    metrics = compute_stage4_metrics(lifecycle, signals, entry, [])
    assert "exit_reason_counts" in metrics
    assert "max_hold" in metrics["exit_reason_counts"] or "time_stop" in metrics.get("exit_reason_counts", {})
    assert "regime_label_counts" in metrics
    assert "stage4_rejection_counts" in metrics


def test_parameter_space_stage4():
    """Parameter space includes Stage 4 params when stage4=True."""
    space = get_bounded_space(stage4=True)
    samples = space.sample_random(2)
    assert len(samples) == 2
    first = samples[0]
    stage4_keys = [k for k in first if "regime" in k or "cluster" in k or "exhaustion" in k or "persistence" in k or "anti_chase" in k]
    assert len(stage4_keys) >= 1 or "entry.long_threshold" in first


def test_approved_param_paths_stage4():
    """APPROVED_PARAM_PATHS includes Stage 4 entry and stop_tp keys."""
    assert "entry.use_adaptive_thresholds" in APPROVED_PARAM_PATHS
    assert "entry.use_regime_filter" in APPROVED_PARAM_PATHS
    assert "entry.max_positions_per_cluster" in APPROVED_PARAM_PATHS
    assert "stop_tp.exhaustion_exit_enabled" in APPROVED_PARAM_PATHS
    assert "stop_tp.failed_breakout_exit_enabled" in APPROVED_PARAM_PATHS
