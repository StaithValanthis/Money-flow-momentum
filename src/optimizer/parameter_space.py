"""Bounded parameter space for optimization (grid/random)."""

from typing import Any, Iterator

# Param paths that must be integers when applied to config (sampling produces ints)
INTEGER_PARAM_KEYS = frozenset({
    "entry.max_positions_per_cluster",
    "stop_tp.time_stop_bars",
})


class ParameterSpace:
    """Defines bounds and optional discrete choices for optimizable params."""

    def __init__(self, bounds: dict[str, tuple[float, float]], discrete: dict[str, list[Any]] | None = None):
        self.bounds = bounds
        self.discrete = discrete or {}

    def sample_random(self, n: int, rng: Any = None) -> list[dict[str, Any]]:
        """Sample n random points (continuous params in bounds). Integer keys are sampled as ints."""
        import random
        if rng is not None:
            rng = rng
        else:
            rng = random.Random()
        out = []
        for _ in range(n):
            point = {}
            for k, (lo, hi) in self.bounds.items():
                if k in self.discrete:
                    point[k] = rng.choice(self.discrete[k])
                else:
                    val = lo + (hi - lo) * rng.random()
                    if k in INTEGER_PARAM_KEYS:
                        val = int(round(val))
                        val = max(int(lo), min(int(hi), val))
                    point[k] = val
            out.append(point)
        return out

    def grid_points(self, n_per_dim: int = 3) -> list[dict[str, Any]]:
        """Simple grid over bounds (small n_per_dim only)."""
        import itertools
        axes = []
        for k, (lo, hi) in self.bounds.items():
            if k in self.discrete:
                axes.append(self.discrete[k])
            else:
                step = (hi - lo) / max(1, n_per_dim - 1)
                axes.append([lo + i * step for i in range(n_per_dim)])
        out = []
        for combo in itertools.product(*axes):
            out.append(dict(zip(self.bounds.keys(), combo)))
        return out


def get_bounded_space(stage4: bool = True, stage5: bool = True) -> ParameterSpace:
    """Default bounded space for approved params only. stage4=True adds Stage 4 params; stage5=True adds Stage 5 params."""
    bounds = {
        "entry.long_threshold": (1.0, 2.5),
        "entry.short_threshold": (-2.5, -1.0),
        "entry.min_delta_1m": (-0.5, 0.5),
        "entry.min_buy_sell_ratio_long": (1.0, 1.2),
        "entry.max_buy_sell_ratio_short": (0.8, 1.0),
        "stop_tp.atr_multiplier_sl": (1.0, 2.5),
        "stop_tp.tp1_r_multiple": (0.5, 1.5),
        "stop_tp.tp2_r_multiple": (1.0, 3.0),
        "stop_tp.tp1_pct": (0.2, 0.5),
        "stop_tp.tp2_pct": (0.2, 0.5),
        "risk.risk_per_trade_pct": (0.2, 1.0),
    }
    discrete: dict[str, list[Any]] = {}
    if stage4:
        bounds.update({
            "entry.anti_chase_penalty": (0.0, 0.3),
            "entry.persistence_bonus": (0.0, 0.2),
            "entry.max_positions_per_cluster": (1, 4),
            "stop_tp.exhaustion_flow_price_ratio_max": (1.0, 3.0),
            "stop_tp.failed_breakout_reversal_pct": (0.001, 0.01),
            "stop_tp.time_stop_vol_multiplier": (0.5, 1.5),
            "entry.use_adaptive_thresholds": (0, 1),
            "entry.use_regime_filter": (0, 1),
            "stop_tp.exhaustion_exit_enabled": (0, 1),
            "stop_tp.failed_breakout_exit_enabled": (0, 1),
            "stop_tp.volatility_aware_time_stop": (0, 1),
        })
        discrete["entry.use_adaptive_thresholds"] = [True, False]
        discrete["entry.use_regime_filter"] = [True, False]
        discrete["stop_tp.exhaustion_exit_enabled"] = [True, False]
        discrete["stop_tp.failed_breakout_exit_enabled"] = [True, False]
        discrete["stop_tp.volatility_aware_time_stop"] = [True, False]
    if stage5:
        bounds.update({
            "risk.allocation_method": (0.0, 1.0),
            "risk.max_cluster_risk_pct": (0.0, 6.0),
            "risk.max_long_risk_pct": (0.0, 6.0),
            "risk.max_short_risk_pct": (0.0, 6.0),
            "portfolio_exposure.max_gross_exposure_per_cluster_pct": (0.0, 50.0),
            "portfolio_exposure.max_risk_per_cluster_pct": (0.0, 12.0),
            "portfolio_exposure.same_direction_concentration_penalty_pct": (0.0, 25.0),
        })
        discrete["risk.allocation_method"] = ["equal_risk", "score_weighted", "capped_score_weighted", "cluster_aware"]
    return ParameterSpace(bounds=bounds, discrete=discrete)
