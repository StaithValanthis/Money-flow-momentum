"""Portfolio-level risk budgeting: long/short/cluster/total risk caps."""

from dataclasses import dataclass, field
from typing import Optional

from src.config.config import RiskConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RiskBudgetState:
    """Current usage of risk budgets."""

    total_risk_usdt: float = 0.0
    long_risk_usdt: float = 0.0
    short_risk_usdt: float = 0.0
    cluster_risk_usdt: dict[int, float] = field(default_factory=dict)
    equity_usdt: float = 10_000.0


@dataclass
class RiskBudgetResult:
    """Result of budget check: allowed, reason if blocked."""

    allowed: bool
    reason: Optional[str] = None
    budget_used_pct: Optional[float] = None


def check_total_risk_budget(
    state: RiskBudgetState,
    config: RiskConfig,
    additional_risk_usdt: float = 0.0,
) -> RiskBudgetResult:
    """Check if total active risk is within max_total_risk_pct."""
    cap_pct = config.max_total_risk_pct
    if cap_pct <= 0:
        return RiskBudgetResult(allowed=True)
    cap_usdt = state.equity_usdt * (cap_pct / 100)
    new_total = state.total_risk_usdt + additional_risk_usdt
    if new_total > cap_usdt:
        return RiskBudgetResult(
            allowed=False,
            reason=f"total_risk_budget:{new_total:.0f}>{cap_usdt:.0f}",
            budget_used_pct=100.0 * state.total_risk_usdt / cap_usdt if cap_usdt else 0,
        )
    return RiskBudgetResult(allowed=True, budget_used_pct=100.0 * new_total / cap_usdt if cap_usdt else 0)


def check_long_risk_budget(
    state: RiskBudgetState,
    config: RiskConfig,
    additional_risk_usdt: float = 0.0,
) -> RiskBudgetResult:
    """Check long-side risk budget. 0 = no separate cap."""
    cap_pct = config.max_long_risk_pct
    if cap_pct <= 0:
        return RiskBudgetResult(allowed=True)
    cap_usdt = state.equity_usdt * (cap_pct / 100)
    new_long = state.long_risk_usdt + additional_risk_usdt
    if new_long > cap_usdt:
        return RiskBudgetResult(
            allowed=False,
            reason=f"long_risk_budget:{new_long:.0f}>{cap_usdt:.0f}",
            budget_used_pct=100.0 * state.long_risk_usdt / cap_usdt if cap_usdt else 0,
        )
    return RiskBudgetResult(allowed=True, budget_used_pct=100.0 * new_long / cap_usdt if cap_usdt else 0)


def check_short_risk_budget(
    state: RiskBudgetState,
    config: RiskConfig,
    additional_risk_usdt: float = 0.0,
) -> RiskBudgetResult:
    """Check short-side risk budget. 0 = no separate cap."""
    cap_pct = config.max_short_risk_pct
    if cap_pct <= 0:
        return RiskBudgetResult(allowed=True)
    cap_usdt = state.equity_usdt * (cap_pct / 100)
    new_short = state.short_risk_usdt + additional_risk_usdt
    if new_short > cap_usdt:
        return RiskBudgetResult(
            allowed=False,
            reason=f"short_risk_budget:{new_short:.0f}>{cap_usdt:.0f}",
            budget_used_pct=100.0 * state.short_risk_usdt / cap_usdt if cap_usdt else 0,
        )
    return RiskBudgetResult(allowed=True, budget_used_pct=100.0 * new_short / cap_usdt if cap_usdt else 0)


def check_cluster_risk_budget(
    state: RiskBudgetState,
    config: RiskConfig,
    cluster_id: int,
    additional_risk_usdt: float = 0.0,
) -> RiskBudgetResult:
    """Check cluster risk budget. 0 = no cap."""
    cap_pct = config.max_cluster_risk_pct
    if cap_pct <= 0:
        return RiskBudgetResult(allowed=True)
    cap_usdt = state.equity_usdt * (cap_pct / 100)
    current = state.cluster_risk_usdt.get(cluster_id, 0.0)
    new_cluster = current + additional_risk_usdt
    if new_cluster > cap_usdt:
        return RiskBudgetResult(
            allowed=False,
            reason=f"cluster_risk_budget:cluster_{cluster_id}:{new_cluster:.0f}>{cap_usdt:.0f}",
            budget_used_pct=100.0 * current / cap_usdt if cap_usdt else 0,
        )
    return RiskBudgetResult(allowed=True, budget_used_pct=100.0 * new_cluster / cap_usdt if cap_usdt else 0)


def build_budget_state(
    equity_usdt: float,
    positions: list[tuple[str, str, float, float]],  # (symbol, side, size, stop_distance_per_unit)
    symbol_to_cluster: dict[str, int],
) -> RiskBudgetState:
    """Build current risk budget state from positions. stop_distance_per_unit = |entry - stop|."""
    state = RiskBudgetState(equity_usdt=equity_usdt)
    for symbol, side, size, dist in positions:
        risk = size * dist
        state.total_risk_usdt += risk
        if side == "Buy":
            state.long_risk_usdt += risk
        else:
            state.short_risk_usdt += risk
        c = symbol_to_cluster.get(symbol, -1)
        if c >= 0:
            state.cluster_risk_usdt[c] = state.cluster_risk_usdt.get(c, 0) + risk
    return state
