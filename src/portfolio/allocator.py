"""Portfolio allocator: allocate risk across candidates (equal_risk, score_weighted, etc.)."""

from dataclasses import dataclass, field
from typing import Optional

from src.config.config import RiskConfig
from src.risk.risk_engine import PositionSizingResult
from src.portfolio.risk_budget import (
    RiskBudgetState,
    check_total_risk_budget,
    check_long_risk_budget,
    check_short_risk_budget,
    check_cluster_risk_budget,
)
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class AllocationDecision:
    """Result of allocation: final qty, notional, risk, and reason if reduced/blocked."""

    qty: float
    notional_usdt: float
    risk_usdt: float
    stop_price: float
    r_multiple: float
    reject_reason: Optional[str] = None
    allocation_reason: Optional[str] = None
    original_qty: float = 0.0
    original_risk_usdt: float = 0.0
    resized: bool = False


@dataclass
class CandidateForAllocation:
    """One candidate with sizing ready for set allocation."""

    symbol: str
    side: str
    score: float
    base_sizing: PositionSizingResult
    cluster_id: Optional[int]
    entry_price: float
    stop_price: float
    atr: float
    qty_step: float
    min_qty: float


def allocate_candidate_set(
    candidates: list[CandidateForAllocation],
    budget_state: RiskBudgetState,
    config: RiskConfig,
    allocation_method: str,
) -> list[tuple[CandidateForAllocation, AllocationDecision]]:
    """
    Allocate across candidate set: apply budgets, prefer higher score when tight.
    Returns list of (candidate, decision). Blocked candidates get reject_reason set.
    """
    if not candidates:
        return []
    decisions: list[tuple[CandidateForAllocation, AllocationDecision]] = []
    state = RiskBudgetState(
        equity_usdt=budget_state.equity_usdt,
        total_risk_usdt=budget_state.total_risk_usdt,
        long_risk_usdt=budget_state.long_risk_usdt,
        short_risk_usdt=budget_state.short_risk_usdt,
        cluster_risk_usdt=dict(budget_state.cluster_risk_usdt),
    )
    cap_total_pct = config.max_total_risk_pct
    cap_total_usdt = (state.equity_usdt * cap_total_pct / 100) if cap_total_pct > 0 else float("inf")
    cap_long_pct = config.max_long_risk_pct
    cap_long_usdt = (state.equity_usdt * cap_long_pct / 100) if cap_long_pct > 0 else float("inf")
    cap_short_pct = config.max_short_risk_pct
    cap_short_usdt = (state.equity_usdt * cap_short_pct / 100) if cap_short_pct > 0 else float("inf")

    sorted_candidates = sorted(candidates, key=lambda c: -abs(c.score))
    for c in sorted_candidates:
        base = c.base_sizing
        if base.reject_reason:
            decisions.append((c, AllocationDecision(
                qty=0, notional_usdt=0, risk_usdt=0, stop_price=base.stop_price, r_multiple=base.r_multiple,
                reject_reason=base.reject_reason, original_qty=base.qty, original_risk_usdt=base.risk_usdt,
            )))
            continue
        risk = base.risk_usdt
        qty = base.qty
        reason: Optional[str] = None
        resized = False

        if state.total_risk_usdt + risk > cap_total_usdt:
            if cap_total_usdt <= state.total_risk_usdt:
                decisions.append((c, AllocationDecision(
                    qty=0, notional_usdt=0, risk_usdt=0, stop_price=base.stop_price, r_multiple=base.r_multiple,
                    reject_reason=f"total_risk_budget:no_room", original_qty=qty, original_risk_usdt=risk,
                )))
                continue
            room = cap_total_usdt - state.total_risk_usdt
            scale = room / risk
            qty = base.qty * scale
            risk = risk * scale
            reason = "total_risk_budget_cap"
            resized = True
        if c.side == "Buy":
            if state.long_risk_usdt + risk > cap_long_usdt and cap_long_usdt < float("inf"):
                if cap_long_usdt <= state.long_risk_usdt:
                    decisions.append((c, AllocationDecision(
                        qty=0, notional_usdt=0, risk_usdt=0, stop_price=base.stop_price, r_multiple=base.r_multiple,
                        reject_reason="long_risk_budget:no_room", original_qty=base.qty, original_risk_usdt=base.risk_usdt,
                    )))
                    continue
                room = cap_long_usdt - state.long_risk_usdt
                scale = room / risk if risk else 0
                qty = base.qty * scale
                risk = risk * scale
                reason = reason or "long_risk_budget_cap"
                resized = True
        else:
            if state.short_risk_usdt + risk > cap_short_usdt and cap_short_usdt < float("inf"):
                if cap_short_usdt <= state.short_risk_usdt:
                    decisions.append((c, AllocationDecision(
                        qty=0, notional_usdt=0, risk_usdt=0, stop_price=base.stop_price, r_multiple=base.r_multiple,
                        reject_reason="short_risk_budget:no_room", original_qty=base.qty, original_risk_usdt=base.risk_usdt,
                    )))
                    continue
                room = cap_short_usdt - state.short_risk_usdt
                scale = room / risk if risk else 0
                qty = base.qty * scale
                risk = risk * scale
                reason = reason or "short_risk_budget_cap"
                resized = True
        if c.cluster_id is not None and c.cluster_id >= 0 and config.max_cluster_risk_pct > 0:
            cap_c = state.equity_usdt * (config.max_cluster_risk_pct / 100)
            current_c = state.cluster_risk_usdt.get(c.cluster_id, 0.0)
            if current_c + risk > cap_c:
                if cap_c <= current_c:
                    decisions.append((c, AllocationDecision(
                        qty=0, notional_usdt=0, risk_usdt=0, stop_price=base.stop_price, r_multiple=base.r_multiple,
                        reject_reason=f"cluster_risk_budget:cluster_{c.cluster_id}:no_room", original_qty=base.qty, original_risk_usdt=base.risk_usdt,
                    )))
                    continue
                room = cap_c - current_c
                scale = room / risk if risk else 0
                qty = base.qty * scale
                risk = risk * scale
                reason = reason or f"cluster_risk_budget_cap_{c.cluster_id}"
                resized = True
        if allocation_method == "capped_score_weighted" and not resized and abs(c.score) > 0:
            n_pos = config.max_concurrent_positions
            if n_pos > 0 and state.equity_usdt > 0:
                equal_share = (config.max_total_risk_pct / 100) * state.equity_usdt / n_pos
                if risk > 2.0 * equal_share:
                    scale = (2.0 * equal_share) / risk
                    qty = base.qty * scale
                    risk = risk * scale
                    reason = "capped_score_weighted"
                    resized = True
        if qty < c.min_qty or qty <= 0:
            decisions.append((c, AllocationDecision(
                qty=0, notional_usdt=0, risk_usdt=0, stop_price=base.stop_price, r_multiple=base.r_multiple,
                reject_reason="allocation_min_qty", original_qty=base.qty, original_risk_usdt=base.risk_usdt,
            )))
            continue
        if c.qty_step > 0:
            qty = max(c.min_qty, round(qty / c.qty_step) * c.qty_step)
        notional = (qty / base.qty * base.notional_usdt) if base.qty else 0
        decisions.append((c, AllocationDecision(
            qty=qty, notional_usdt=notional, risk_usdt=risk, stop_price=base.stop_price, r_multiple=base.r_multiple,
            allocation_reason=reason, original_qty=base.qty, original_risk_usdt=base.risk_usdt, resized=resized,
        )))
        state.total_risk_usdt += risk
        if c.side == "Buy":
            state.long_risk_usdt += risk
        else:
            state.short_risk_usdt += risk
        if c.cluster_id is not None and c.cluster_id >= 0:
            state.cluster_risk_usdt[c.cluster_id] = state.cluster_risk_usdt.get(c.cluster_id, 0) + risk
    return decisions


def allocate_risk(
    base_sizing: PositionSizingResult,
    symbol: str,
    side: str,
    score: float,
    cluster_id: Optional[int],
    budget_state: RiskBudgetState,
    config: RiskConfig,
    allocation_method: str,
    candidate_risk_usdt: float,
) -> AllocationDecision:
    """
    Apply portfolio allocation: may reduce size to fit budgets. base_sizing is from RiskEngine.
    candidate_risk_usdt = qty * |entry - stop| from base_sizing.
    """
    if base_sizing.reject_reason:
        return AllocationDecision(
            qty=0,
            notional_usdt=0,
            risk_usdt=0,
            stop_price=base_sizing.stop_price,
            r_multiple=base_sizing.r_multiple,
            reject_reason=base_sizing.reject_reason,
        )
    qty = base_sizing.qty
    risk = candidate_risk_usdt
    reason: Optional[str] = None

    r_total = check_total_risk_budget(budget_state, config, additional_risk_usdt=risk)
    if not r_total.allowed:
        return AllocationDecision(
            qty=0, notional_usdt=0, risk_usdt=0, stop_price=base_sizing.stop_price,
            r_multiple=base_sizing.r_multiple, reject_reason=r_total.reason,
        )
    if side == "Buy":
        r_side = check_long_risk_budget(budget_state, config, additional_risk_usdt=risk)
    else:
        r_side = check_short_risk_budget(budget_state, config, additional_risk_usdt=risk)
    if not r_side.allowed:
        return AllocationDecision(
            qty=0, notional_usdt=0, risk_usdt=0, stop_price=base_sizing.stop_price,
            r_multiple=base_sizing.r_multiple, reject_reason=r_side.reason,
        )
    if cluster_id is not None and cluster_id >= 0:
        r_cluster = check_cluster_risk_budget(budget_state, config, cluster_id, additional_risk_usdt=risk)
        if not r_cluster.allowed:
            return AllocationDecision(
                qty=0, notional_usdt=0, risk_usdt=0, stop_price=base_sizing.stop_price,
                r_multiple=base_sizing.r_multiple, reject_reason=r_cluster.reason,
            )

    scale = 1.0
    if allocation_method == "capped_score_weighted" and score != 0:
        n_positions = config.max_concurrent_positions
        if n_positions > 0 and budget_state.equity_usdt > 0:
            equal_share_risk = (config.max_total_risk_pct / 100) * budget_state.equity_usdt / n_positions
            if risk > 2.0 * equal_share_risk:
                scale = (2.0 * equal_share_risk) / risk
                reason = "capped_score_weighted"
    if scale < 1.0 and scale > 0:
        qty = qty * scale
        risk = risk * scale
    if base_sizing.qty and base_sizing.notional_usdt:
        notional = (qty / base_sizing.qty) * base_sizing.notional_usdt
    else:
        notional = base_sizing.notional_usdt * scale if base_sizing.notional_usdt else 0

    return AllocationDecision(
        qty=qty,
        notional_usdt=notional,
        risk_usdt=risk,
        stop_price=base_sizing.stop_price,
        r_multiple=base_sizing.r_multiple,
        allocation_reason=reason,
    )
