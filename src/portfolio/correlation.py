"""Portfolio correlation proxy and cluster controls."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.data.feature_builder import SymbolFeatures
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SymbolCluster:
    """Cluster id and members."""

    cluster_id: int
    symbols: list[str]


def rolling_return_proxy(features_list: list[SymbolFeatures]) -> np.ndarray:
    """Return matrix of price_return_1m for correlation proxy (N x 1). For multi-symbol we'd need history; use single snapshot as similarity proxy."""
    returns = np.array([f.price_return_1m for f in features_list])
    return returns


def correlation_proxy_matrix(features_list: list[SymbolFeatures]) -> np.ndarray:
    """
    Simple similarity: symbols with similar return and delta sign get high correlation proxy.
    Returns N x N matrix of pairwise similarity in [0, 1].
    """
    n = len(features_list)
    if n == 0:
        return np.array([])
    rets = np.array([f.price_return_1m for f in features_list])
    deltas = np.array([f.delta_1m for f in features_list])
    delta_sign = np.sign(deltas)
    sim = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                sim[i, j] = 1.0
            else:
                ret_sim = 1.0 - min(1.0, abs(rets[i] - rets[j]) * 100)
                sign_agree = 1.0 if delta_sign[i] == delta_sign[j] else 0.0
                sim[i, j] = 0.5 * ret_sim + 0.5 * sign_agree
    return sim


def cluster_by_correlation_proxy(
    features_list: list[SymbolFeatures],
    correlation_threshold: float = 0.7,
) -> dict[str, int]:
    """
    Assign cluster_id per symbol using correlation proxy. Symbols with similarity >= threshold share a cluster.
    Returns symbol -> cluster_id (0, 1, 2, ...).
    """
    if not features_list:
        return {}
    syms = [f.symbol for f in features_list]
    sim = correlation_proxy_matrix(features_list)
    n = len(syms)
    assigned = [-1] * n
    cluster_id = 0
    for i in range(n):
        if assigned[i] >= 0:
            continue
        assigned[i] = cluster_id
        for j in range(i + 1, n):
            if assigned[j] < 0 and sim[i, j] >= correlation_threshold:
                assigned[j] = cluster_id
        cluster_id += 1
    return {syms[i]: assigned[i] for i in range(n)}


def cluster_counts_per_side(
    symbol_to_cluster: dict[str, int],
    long_symbols: list[str],
    short_symbols: list[str],
) -> dict[int, int]:
    """Count positions per cluster (longs + shorts)."""
    counts: dict[int, int] = {}
    for s in long_symbols + short_symbols:
        c = symbol_to_cluster.get(s, -1)
        if c >= 0:
            counts[c] = counts.get(c, 0) + 1
    return counts


def cluster_blocked(
    symbol: str,
    symbol_to_cluster: dict[str, int],
    current_long_symbols: list[str],
    current_short_symbols: list[str],
    max_per_cluster: int,
) -> bool:
    """True if adding this symbol would exceed max positions in its cluster."""
    c = symbol_to_cluster.get(symbol, -1)
    if c < 0 or max_per_cluster <= 0:
        return False
    counts = cluster_counts_per_side(symbol_to_cluster, current_long_symbols, current_short_symbols)
    return counts.get(c, 0) >= max_per_cluster
