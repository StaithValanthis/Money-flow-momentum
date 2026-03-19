"""
Warm-start seed acceptance: stricter quality/realism checks before a replay winner
is auto-activated for Demo. Replay PnL alone is not sufficient.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.config.config import Config, WarmStartConfig
from src.warm_start.research_validation import research_layers_failed


def _get_acceptance_config(config: Config) -> WarmStartConfig:
    return getattr(config, "warm_start", None) or WarmStartConfig()


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    n = len(values)
    s = sorted(values)
    return (s[n // 2] + s[(n - 1) // 2]) / 2.0 if n else 0.0


def passes_warm_start_seed_acceptance(
    metrics: Dict[str, Any],
    config: Config,
    durations_sec: Optional[List[float]] = None,
    fees_summary: float = 0.0,
    slippage_summary: float = 0.0,
    initial_equity: float = 10_000.0,
    research_summary: Optional[Dict[str, Any]] = None,
    family_diagnostics: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluate replay metrics and duration stats against warm-start acceptance thresholds.
    Returns (passed, rejection_reason, checks_dict).
    If passed is True, rejection_reason is empty. checks_dict is always populated for reporting.
    """
    warm = _get_acceptance_config(config)
    durations_sec = durations_sec or []
    n_trades = int(metrics.get("trade_count") or 0)
    closed_trade_count = len(durations_sec) if durations_sec else (n_trades // 2)
    total_pnl = float(metrics.get("total_pnl") or 0)
    win_rate = float(metrics.get("win_rate") or 0)
    profit_factor = float(metrics.get("profit_factor") or 0)
    payoff_ratio = float(metrics.get("payoff_ratio") or 0)
    max_drawdown = float(metrics.get("max_drawdown") or 0)
    return_pct = float(metrics.get("return_pct") or 0)

    median_duration = _median(durations_sec) if durations_sec else 0.0
    ultra_short_threshold = float(getattr(warm, "ultra_short_duration_sec", 60.0))
    ultra_short_count = sum(1 for d in durations_sec if d < ultra_short_threshold)
    ultra_short_fraction = (ultra_short_count / len(durations_sec)) if durations_sec else 0.0

    checks: Dict[str, Any] = {
        "trade_count": n_trades,
        "closed_trade_count": closed_trade_count,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "max_drawdown": max_drawdown,
        "return_pct": return_pct,
        "median_trade_duration_sec": round(median_duration, 2),
        "ultra_short_trade_count": ultra_short_count,
        "ultra_short_trade_fraction": round(ultra_short_fraction, 4),
        "fees_summary": fees_summary,
        "slippage_summary": slippage_summary,
        "family_overfitting_diagnostics": family_diagnostics or {},
        "research_validation": research_summary or {},
    }

    min_trade_count = int(getattr(warm, "min_replay_trade_count", 30))
    if closed_trade_count < min_trade_count:
        checks["warm_start_rejection_attribution"] = "likely_poor_edge"
        return False, f"trade_count_below_min_{closed_trade_count}_<_{min_trade_count}", checks

    if getattr(warm, "require_profitable_seed", True) and total_pnl <= 0:
        checks["warm_start_rejection_attribution"] = "likely_poor_edge"
        return False, "replay_not_profitable", checks

    min_wr = float(getattr(warm, "min_win_rate", 0.18))
    if win_rate < min_wr:
        return False, f"win_rate_below_min_{win_rate:.3f}_<_{min_wr}", checks

    min_pf = float(getattr(warm, "min_profit_factor", 1.10))
    if profit_factor < min_pf:
        return False, f"profit_factor_below_min_{profit_factor:.3f}_<_{min_pf}", checks

    min_pr = float(getattr(warm, "min_payoff_ratio", 1.20))
    avg_loss = float(metrics.get("avg_loss") or 0)
    if avg_loss != 0 and payoff_ratio < min_pr:
        return False, f"payoff_ratio_below_min_{payoff_ratio:.3f}_<_{min_pr}", checks

    max_dd = float(getattr(warm, "max_replay_drawdown", 10.0))
    if max_drawdown > max_dd:
        return False, f"max_drawdown_above_max_{max_drawdown:.2f}_>_{max_dd}", checks

    min_median_dur = float(getattr(warm, "min_median_trade_duration_sec", 120.0))
    if median_duration < min_median_dur:
        return False, f"median_trade_duration_below_min_{median_duration:.0f}s_<_{min_median_dur}s", checks

    max_ultra = float(getattr(warm, "max_ultra_short_trade_fraction", 0.25))
    if ultra_short_fraction > max_ultra:
        return False, f"ultra_short_trade_fraction_above_max_{ultra_short_fraction:.2f}_>_{max_ultra}", checks

    if getattr(warm, "reject_zero_fee_zero_slippage_only_edges", True):
        if (fees_summary == 0 and slippage_summary == 0) or (fees_summary is None and slippage_summary is None):
            min_margin_pct = float(getattr(warm, "min_profit_margin_pct_when_zero_fee", 0.5))
            if return_pct < min_margin_pct:
                return False, f"zero_fee_razor_thin_edge_return_pct_{return_pct:.2f}_<_{min_margin_pct}", checks
            if profit_factor < 1.05:
                return False, f"zero_fee_razor_thin_profit_factor_{profit_factor:.3f}_<_1.05", checks

    # Protection-aware thresholds (when metrics from protection-aware backtest are present)
    stop_out_rate = metrics.get("stop_out_rate")
    if stop_out_rate is not None:
        checks["stop_out_rate"] = stop_out_rate
        max_sor = float(getattr(warm, "max_stop_out_rate", 0.55))
        if stop_out_rate > max_sor:
            return False, f"stop_out_rate_above_max_{stop_out_rate:.2f}_>_{max_sor}", checks

    max_consec_losses = metrics.get("max_consecutive_losses")
    if max_consec_losses is not None:
        checks["max_consecutive_losses"] = max_consec_losses
        max_allowed = int(getattr(warm, "max_consecutive_losses", 6))
        if max_consec_losses > max_allowed:
            return False, f"max_consecutive_losses_above_max_{max_consec_losses}_>_{max_allowed}", checks

    tp1_hit_rate = metrics.get("tp1_hit_rate")
    if tp1_hit_rate is not None:
        checks["tp1_hit_rate"] = tp1_hit_rate
        min_tp1 = float(getattr(warm, "min_tp1_hit_rate", 0.05))
        if tp1_hit_rate < min_tp1:
            checks["warm_start_rejection_attribution"] = "likely_too_tight_protection"
            return False, f"tp1_hit_rate_below_min_{tp1_hit_rate:.2f}_<_{min_tp1}", checks

    fam = family_diagnostics or {}
    orisk = float(fam.get("overfitting_risk") or 0.0)
    if getattr(warm, "reject_on_high_overfitting_risk", False):
        mx = float(getattr(warm, "max_acceptable_overfitting_risk", 0.75))
        if orisk > mx:
            checks["warm_start_rejection_attribution"] = "likely_overfit"
            return False, f"overfitting_risk_above_max_{orisk:.3f}_>_{mx}", checks

    rs = research_summary or {}
    if getattr(warm, "reject_on_research_validation_failure", True) and not rs.get("research_layers_skipped"):
        failed = research_layers_failed(rs, warm)
        if failed:
            if "cost_sensitivity" in failed:
                checks["warm_start_rejection_attribution"] = "likely_cost_fragile"
            elif "regime" in failed:
                checks["warm_start_rejection_attribution"] = "likely_regime_fragile"
            elif "multi_window" in failed:
                checks["warm_start_rejection_attribution"] = "likely_regime_fragile"
            else:
                checks["warm_start_rejection_attribution"] = "mixed_or_unclear"
            return False, f"research_validation_failed_{'_'.join(failed)}", checks

    checks["warm_start_rejection_attribution"] = None
    return True, "", checks
