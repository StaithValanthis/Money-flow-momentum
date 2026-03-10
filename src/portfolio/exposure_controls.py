"""Exposure controls: max gross/risk per cluster, correlated positions, concentration penalty."""

from dataclasses import dataclass
from typing import Optional

from src.config.config import PortfolioExposureConfig
from src.portfolio.correlation import cluster_counts_per_side, cluster_by_correlation_proxy
from src.data.feature_builder import SymbolFeatures
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ExposureCheckResult:
    """Result of exposure check."""

    allowed: bool
    reason: Optional[str] = None
    penalty_pct: float = 0.0


def check_cluster_gross_exposure(
    symbol: str,
    side: str,
    notional_usdt: float,
    cluster_id: int,
    current_long_notional: float,
    current_short_notional: float,
    cluster_notionals: dict[int, tuple[float, float]],  # cluster_id -> (long_notional, short_notional)
    equity_usdt: float,
    config: PortfolioExposureConfig,
) -> ExposureCheckResult:
    """Check if adding this position exceeds max gross exposure per cluster."""
    if config.max_gross_exposure_per_cluster_pct <= 0:
        return ExposureCheckResult(allowed=True)
    cap = equity_usdt * (config.max_gross_exposure_per_cluster_pct / 100)
    long_n, short_n = cluster_notionals.get(cluster_id, (0.0, 0.0))
    if side == "Buy":
        new_gross = long_n + short_n + notional_usdt
    else:
        new_gross = long_n + short_n + notional_usdt
    if new_gross > cap:
        return ExposureCheckResult(
            allowed=False,
            reason=f"cluster_gross_exposure:cluster_{cluster_id}:{new_gross:.0f}>{cap:.0f}",
        )
    return ExposureCheckResult(allowed=True)


def check_cluster_risk_exposure(
    cluster_risk_usdt: float,
    additional_risk_usdt: float,
    cluster_id: int,
    equity_usdt: float,
    config: PortfolioExposureConfig,
) -> ExposureCheckResult:
    """Check max risk per cluster."""
    if config.max_risk_per_cluster_pct <= 0:
        return ExposureCheckResult(allowed=True)
    cap = equity_usdt * (config.max_risk_per_cluster_pct / 100)
    new_risk = cluster_risk_usdt + additional_risk_usdt
    if new_risk > cap:
        return ExposureCheckResult(
            allowed=False,
            reason=f"cluster_risk_exposure:cluster_{cluster_id}:{new_risk:.0f}>{cap:.0f}",
        )
    return ExposureCheckResult(allowed=True)


def same_direction_concentration_penalty(
    long_count: int,
    short_count: int,
    side: str,
    config: PortfolioExposureConfig,
) -> float:
    """Return penalty pct (0-1) when same-direction concentration is high. Applied as score penalty."""
    if config.same_direction_concentration_penalty_pct <= 0:
        return 0.0
    if side == "Buy":
        n = long_count
    else:
        n = short_count
    if n <= 0:
        return 0.0
    # Penalize when we already have many same-side positions (e.g. 2+ longs -> small penalty)
    if n >= 2:
        return min(1.0, (config.same_direction_concentration_penalty_pct / 100) * (n - 1) * 0.5)
    return 0.0


def cluster_notionals_from_positions(
    positions: list[tuple[str, str, float, float]],  # symbol, side, size, entry_price
    symbol_to_cluster: dict[str, int],
) -> dict[int, tuple[float, float]]:
    """Aggregate notional per cluster (long_notional, short_notional)."""
    out: dict[int, tuple[float, float]] = {}
    for symbol, side, size, price in positions:
        notional = size * price
        c = symbol_to_cluster.get(symbol, -1)
        if c < 0:
            continue
        l_n, s_n = out.get(c, (0.0, 0.0))
        if side == "Buy":
            l_n += notional
        else:
            s_n += notional
        out[c] = (l_n, s_n)
    return out
