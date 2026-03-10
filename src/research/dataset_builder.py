"""Build analysis/replay datasets from stored bot data."""

from typing import Optional

from src.storage.db import Database


def build_analysis_dataset(
    db_path: str = "data/bot.db",
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    config_id: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    dry_run_only: bool = False,
) -> dict:
    """
    Build a single dataset dict from DB for evaluation/optimization.
    Filter by date range, config_id, symbols. dry_run_only filters entry_decisions by dry_run=1.
    """
    db = Database(db_path)
    trades = db.get_trades(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
    fills = db.get_fills(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
    entry_decisions = db.get_entry_decisions(
        since_ts=from_ts, to_ts=to_ts, config_id=config_id, symbol=None
    )
    lifecycle = db.get_lifecycle_events(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
    equity = db.get_equity_curve(since_ts=from_ts, to_ts=to_ts)
    signals = db.get_signal_snapshots(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
    db.close()

    if symbols:
        sym_set = set(symbols)
        trades = [t for t in trades if t.get("symbol") in sym_set]
        fills = [f for f in fills if f.get("symbol") in sym_set]
        entry_decisions = [d for d in entry_decisions if d.get("symbol") in sym_set]
        lifecycle = [e for e in lifecycle if e.get("symbol") in sym_set]
        signals = [s for s in signals if s.get("symbol") in sym_set]

    if dry_run_only:
        entry_decisions = [d for d in entry_decisions if d.get("dry_run") == 1]

    return {
        "trades": trades,
        "fills": fills,
        "entry_decisions": entry_decisions,
        "lifecycle_events": lifecycle,
        "equity_curve": equity,
        "signal_snapshots": signals,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "config_id": config_id,
    }
