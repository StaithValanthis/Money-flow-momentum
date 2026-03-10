"""Overfitting guardrails: reject or penalize unstable/overfit candidates."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class GuardrailResult:
    passed: bool
    reason_codes: list[str]
    penalty: float


def check_guardrails(
    in_sample_metrics: dict[str, Any],
    out_of_sample_metrics: dict[str, Any],
    baseline_metrics: Optional[dict[str, Any]] = None,
    min_trades: int = 15,
    max_symbol_concentration_pct: float = 60.0,
    max_oos_degradation_pct: float = 30.0,
    min_improvement_vs_baseline_pct: float = 0.0,
    max_drawdown_worse_than_baseline_pct: float = 5.0,
) -> GuardrailResult:
    """
    Reject or penalize if:
    - trade count too low
    - performance dominated by one symbol
    - OOS degrades materially vs IS
    - drawdown materially worse than baseline
    - improvement vs baseline too small
    """
    reasons: list[str] = []
    penalty = 0.0

    oos_trades = int(out_of_sample_metrics.get("trade_count") or 0)
    if oos_trades < min_trades:
        reasons.append("low_oos_trade_count")
        penalty += 1.0

    is_ret = float(in_sample_metrics.get("return_pct") or 0)
    oos_ret = float(out_of_sample_metrics.get("return_pct") or 0)
    if is_ret > 0 and oos_ret < is_ret * (1 - max_oos_degradation_pct / 100):
        reasons.append("oos_degradation")
        penalty += 0.8

    oos_dd = float(out_of_sample_metrics.get("max_drawdown") or 0)
    if baseline_metrics is not None:
        base_dd = float(baseline_metrics.get("max_drawdown") or 0)
        if oos_dd > base_dd + max_drawdown_worse_than_baseline_pct:
            reasons.append("drawdown_worse_than_baseline")
            penalty += 0.7
        base_ret = float(baseline_metrics.get("return_pct") or 0)
        if oos_ret - base_ret < min_improvement_vs_baseline_pct:
            reasons.append("insufficient_improvement_vs_baseline")
            penalty += 0.3

    passed = len(reasons) == 0 and penalty == 0
    return GuardrailResult(passed=passed, reason_codes=reasons, penalty=penalty)


def check_symbol_concentration(by_symbol_metrics: dict[str, dict], max_pct: float = 60.0) -> GuardrailResult:
    """Reject if one symbol dominates total PnL."""
    reasons = []
    penalty = 0.0
    total_pnl = sum(m.get("total_pnl", 0) for m in by_symbol_metrics.values())
    if total_pnl == 0:
        return GuardrailResult(passed=True, reason_codes=[], penalty=0.0)
    for sym, m in by_symbol_metrics.items():
        pct = 100.0 * abs(m.get("total_pnl", 0)) / abs(total_pnl)
        if pct >= max_pct:
            reasons.append(f"symbol_concentration_{sym}")
            penalty += 0.5
    return GuardrailResult(passed=len(reasons) == 0, reason_codes=reasons, penalty=penalty)
