"""Dataset loading helpers for evaluation/optimization (filter by date, symbol, config)."""

from collections import defaultdict
from typing import Optional

from src.storage.db import Database


def _is_entry_row(t: dict) -> bool:
    link = (t.get("order_link_id") or "") or ""
    return link.startswith("entry")


def _is_exit_row(t: dict) -> bool:
    link = (t.get("order_link_id") or "") or ""
    return link.startswith("tp1_") or link.startswith("tp2_")


def compute_realized_pnl_by_pairing(trades: list[dict]) -> list[dict]:
    """
    When trades have no PnL (e.g. execution stream did not send execPnl), compute realized PnL
    by pairing entry and exit rows per symbol (FIFO). Supports long/short and partial TP exits.

    Entry = order_link_id startswith "entry"; Exit = order_link_id startswith "tp1_" or "tp2_".
    Flow-reversal / reduce-only closes without tp link are not paired (limitation).

    Returns a copy of trades with pnl set on exit rows where pairing succeeded.
    """
    if not trades:
        return []
    out = [dict(t) for t in trades]
    ts_key = lambda r: int(r.get("ts") or 0)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for i, t in enumerate(out):
        by_symbol[t.get("symbol") or ""].append((i, t))

    for symbol, indexed in by_symbol.items():
        if not symbol:
            continue
        indexed.sort(key=lambda x: ts_key(x[1]))
        # FIFO entry queues per side (long = Buy entry, short = Sell entry)
        entry_queue_long: list[tuple[float, float, int]] = []  # (qty_left, price, ts)
        entry_queue_short: list[tuple[float, float, int]] = []
        exit_pnl: dict[int, float] = defaultdict(float)

        for idx, t in indexed:
            link = (t.get("order_link_id") or "") or ""
            side = (t.get("side") or "").strip()
            qty = float(t.get("qty") or 0)
            price = float(t.get("price") or 0)
            ts = ts_key(t)
            if qty <= 0:
                continue
            if _is_entry_row(t):
                if side == "Buy":
                    entry_queue_long.append((qty, price, ts))
                elif side == "Sell":
                    entry_queue_short.append((qty, price, ts))
                continue
            if not _is_exit_row(t):
                continue
            exit_qty_left = qty
            # Exit Sell closes Long (Buy entries); Exit Buy closes Short (Sell entries)
            if side == "Sell":
                while exit_qty_left > 1e-9 and entry_queue_long:
                    entry_qty, entry_price, _ = entry_queue_long[0]
                    match_qty = min(exit_qty_left, entry_qty)
                    exit_pnl[idx] += (price - entry_price) * match_qty
                    exit_qty_left = round(exit_qty_left - match_qty, 8)
                    entry_qty = round(entry_qty - match_qty, 8)
                    if entry_qty <= 1e-9:
                        entry_queue_long.pop(0)
                    else:
                        entry_queue_long[0] = (entry_qty, entry_price, _)
            elif side == "Buy":
                while exit_qty_left > 1e-9 and entry_queue_short:
                    entry_qty, entry_price, _ = entry_queue_short[0]
                    match_qty = min(exit_qty_left, entry_qty)
                    exit_pnl[idx] += (entry_price - price) * match_qty
                    exit_qty_left = round(exit_qty_left - match_qty, 8)
                    entry_qty = round(entry_qty - match_qty, 8)
                    if entry_qty <= 1e-9:
                        entry_queue_short.pop(0)
                    else:
                        entry_queue_short[0] = (entry_qty, entry_price, _)

        for idx, pnl in exit_pnl.items():
            if idx < len(out):
                out[idx]["pnl"] = pnl
    return out


def get_trade_durations_sec(trades: list[dict]) -> list[float]:
    """
    Pair entry/exit rows per symbol (FIFO) and return list of trade durations in seconds
    (exit_ts - entry_ts for each fully closed position). Used for warm-start acceptance
    (median duration, ultra-short fraction).
    """
    if not trades:
        return []
    ts_key = lambda r: int(r.get("ts") or 0)
    by_symbol: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, t in enumerate(trades):
        by_symbol[t.get("symbol") or ""].append((i, t))

    durations: list[float] = []
    for symbol, indexed in by_symbol.items():
        if not symbol:
            continue
        indexed.sort(key=lambda x: ts_key(x[1]))
        entry_queue_long: list[tuple[float, float, int]] = []  # (qty_left, price, entry_ts)
        entry_queue_short: list[tuple[float, float, int]] = []

        for _idx, t in indexed:
            link = (t.get("order_link_id") or "") or ""
            side = (t.get("side") or "").strip()
            qty = float(t.get("qty") or 0)
            price = float(t.get("price") or 0)
            ts = ts_key(t)
            if qty <= 0:
                continue
            if _is_entry_row(t):
                if side == "Buy":
                    entry_queue_long.append((qty, price, ts))
                elif side == "Sell":
                    entry_queue_short.append((qty, price, ts))
                continue
            if not _is_exit_row(t):
                continue
            exit_qty_left = qty
            if side == "Sell":
                while exit_qty_left > 1e-9 and entry_queue_long:
                    entry_qty, entry_price, entry_ts = entry_queue_long[0]
                    match_qty = min(exit_qty_left, entry_qty)
                    exit_qty_left = round(exit_qty_left - match_qty, 8)
                    entry_qty = round(entry_qty - match_qty, 8)
                    if entry_qty <= 1e-9:
                        entry_queue_long.pop(0)
                        durations.append((ts - entry_ts) / 1000.0)
                    else:
                        entry_queue_long[0] = (entry_qty, entry_price, entry_ts)
            elif side == "Buy":
                while exit_qty_left > 1e-9 and entry_queue_short:
                    entry_qty, entry_price, entry_ts = entry_queue_short[0]
                    match_qty = min(exit_qty_left, entry_qty)
                    exit_qty_left = round(exit_qty_left - match_qty, 8)
                    entry_qty = round(entry_qty - match_qty, 8)
                    if entry_qty <= 1e-9:
                        entry_queue_short.pop(0)
                        durations.append((ts - entry_ts) / 1000.0)
                    else:
                        entry_queue_short[0] = (entry_qty, entry_price, entry_ts)
    return durations


def load_evaluation_dataset(
    db_path: str = "data/bot.db",
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    config_id: Optional[str] = None,
    symbols: Optional[list[str]] = None,
) -> dict:
    """
    Load trades, fills, entry_decisions, lifecycle_events, equity_curve for a window.
    Returns dict of lists; filter symbols in memory if provided.
    """
    db = Database(db_path)
    trades = db.get_trades(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
    fills = db.get_fills(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
    entry_decisions = db.get_entry_decisions(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
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

    return {
        "trades": trades,
        "fills": fills,
        "entry_decisions": entry_decisions,
        "lifecycle_events": lifecycle,
        "equity_curve": equity,
        "signal_snapshots": signals,
    }
