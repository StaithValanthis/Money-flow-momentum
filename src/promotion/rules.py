"""Promotion eligibility rules: min sample, OOS, drawdown, improvement threshold."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PromotionRules:
    min_trade_count: int = 30
    min_shadow_decisions: int = 50
    max_drawdown_pct: float = 15.0
    min_oos_return_pct: float = -5.0
    min_improvement_vs_baseline_pct: float = 0.0
    allow_auto_promotion: bool = False


def check_promotion_eligibility(
    candidate_metrics: dict[str, Any],
    baseline_metrics: Optional[dict[str, Any]] = None,
    shadow_decision_count: int = 0,
    rules: Optional[PromotionRules] = None,
) -> tuple[bool, list[str]]:
    """
    Returns (eligible, reason_codes). Candidate eligible only if all checks pass.
    """
    rules = rules or PromotionRules()
    reasons = []

    if candidate_metrics.get("trade_count", 0) < rules.min_trade_count:
        reasons.append("insufficient_trade_count")
    if shadow_decision_count > 0 and shadow_decision_count < rules.min_shadow_decisions:
        reasons.append("insufficient_shadow_sample")
    if float(candidate_metrics.get("max_drawdown") or 0) > rules.max_drawdown_pct:
        reasons.append("drawdown_exceeds_limit")
    if float(candidate_metrics.get("return_pct") or 0) < rules.min_oos_return_pct:
        reasons.append("return_below_minimum")
    if baseline_metrics is not None and rules.min_improvement_vs_baseline_pct > 0:
        base_ret = float(baseline_metrics.get("return_pct") or 0)
        cand_ret = float(candidate_metrics.get("return_pct") or 0)
        if cand_ret - base_ret < rules.min_improvement_vs_baseline_pct:
            reasons.append("insufficient_improvement")

    return (len(reasons) == 0, reasons)
