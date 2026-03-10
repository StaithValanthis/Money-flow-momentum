"""Approximate replay of decision logic using saved observations. Limitations: not tick-accurate."""

from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


class ReplayEngine:
    """
    Replay decisions from stored signal snapshots and entry_decisions.
    Uses saved observations only; no live market data. Not tick-accurate.
    """

    def __init__(self, dataset: dict):
        self.trades = dataset.get("trades") or []
        self.entry_decisions = dataset.get("entry_decisions") or []
        self.signal_snapshots = dataset.get("signal_snapshots") or []
        self.lifecycle_events = dataset.get("lifecycle_events") or []

    def replay_decisions(
        self,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> list[dict]:
        """Return entry_decisions in window (no re-scoring; use stored decisions)."""
        out = list(self.entry_decisions)
        if from_ts is not None:
            out = [d for d in out if (d.get("ts") or 0) >= from_ts]
        if to_ts is not None:
            out = [d for d in out if (d.get("ts") or 0) <= to_ts]
        return sorted(out, key=lambda d: d.get("ts") or 0)

    def replay_trades_in_window(
        self,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> list[dict]:
        """Return trades in window."""
        out = list(self.trades)
        if from_ts is not None:
            out = [t for t in out if (t.get("ts") or 0) >= from_ts]
        if to_ts is not None:
            out = [t for t in out if (t.get("ts") or 0) <= to_ts]
        return sorted(out, key=lambda t: t.get("ts") or 0)

    def signal_overlap(self, other: "ReplayEngine", from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> dict[str, Any]:
        """Compare signal snapshots between this and another replay (e.g. baseline vs candidate)."""
        a = [s for s in self.signal_snapshots if (from_ts is None or (s.get("ts") or 0) >= from_ts) and (to_ts is None or (s.get("ts") or 0) <= to_ts)]
        b = [s for s in other.signal_snapshots if (from_ts is None or (s.get("ts") or 0) >= from_ts) and (to_ts is None or (s.get("ts") or 0) <= to_ts)]
        a_by_ts_sym = {(s.get("ts"), s.get("symbol")): s for s in a}
        b_by_ts_sym = {(s.get("ts"), s.get("symbol")): s for s in b}
        common_keys = set(a_by_ts_sym) & set(b_by_ts_sym)
        same_direction = sum(1 for k in common_keys if a_by_ts_sym[k].get("direction") == b_by_ts_sym[k].get("direction"))
        return {
            "count_a": len(a),
            "count_b": len(b),
            "overlap_count": len(common_keys),
            "same_direction_count": same_direction,
            "agreement_rate": same_direction / len(common_keys) if common_keys else 0.0,
        }
