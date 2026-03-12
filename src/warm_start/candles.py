"""Historical candle fetch and cache for warm-start calibration. Demo-safe, read-only."""

import json
import time
from pathlib import Path
from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)

# Bybit kline list item: [startTime, open, high, low, close, volume, turnover]
KLINE_OPEN = 1
KLINE_HIGH = 2
KLINE_LOW = 3
KLINE_CLOSE = 4
KLINE_START_TIME = 0


def _parse_candle(item: Any) -> Optional[dict]:
    """Parse one kline from API (list or dict) to {start_ts, open, high, low, close}."""
    if isinstance(item, list) and len(item) > 4:
        try:
            return {
                "start_ts": int(item[KLINE_START_TIME]),
                "open": float(item[KLINE_OPEN]),
                "high": float(item[KLINE_HIGH]),
                "low": float(item[KLINE_LOW]),
                "close": float(item[KLINE_CLOSE]),
            }
        except (ValueError, TypeError):
            return None
    if isinstance(item, dict):
        try:
            return {
                "start_ts": int(item.get("startTime") or item.get("start") or 0),
                "open": float(item.get("open") or item.get("o", 0)),
                "high": float(item.get("high") or item.get("h", 0)),
                "low": float(item.get("low") or item.get("l", 0)),
                "close": float(item.get("close") or item.get("c", 0)),
            }
        except (ValueError, TypeError):
            return None
    return None


def fetch_klines_chunk(
    client: Any,
    symbol: str,
    interval: str,
    end_ts_ms: Optional[int] = None,
    limit: int = 200,
) -> list[dict]:
    """Fetch one chunk of klines (forward in time: oldest first in Bybit response when using end)."""
    end_ts_ms = end_ts_ms or int(time.time() * 1000)
    resp = client.get_klines(
        category="linear",
        symbol=symbol,
        interval=interval,
        end=end_ts_ms,
        limit=limit,
    )
    lst = resp.get("result", {}).get("list", [])
    if not lst:
        return []
    out = []
    for item in lst:
        c = _parse_candle(item)
        if c:
            out.append(c)
    return out


def fetch_klines_for_symbol(
    client: Any,
    symbol: str,
    interval: str,
    from_ts_ms: int,
    to_ts_ms: int,
    limit_per_request: int = 200,
) -> list[dict]:
    """Fetch klines for a symbol in [from_ts_ms, to_ts_ms], paginating backwards from to_ts_ms."""
    candles: list[dict] = []
    end = to_ts_ms
    while end > from_ts_ms:
        chunk = fetch_klines_chunk(client, symbol, interval, end_ts_ms=end, limit=limit_per_request)
        if not chunk:
            break
        for c in chunk:
            if from_ts_ms <= c["start_ts"] <= to_ts_ms:
                candles.append(c)
        end = min(c["start_ts"] for c in chunk) - 1
        if len(chunk) < limit_per_request:
            break
    candles.sort(key=lambda x: x["start_ts"])
    return candles


def candles_to_synthetic_trades(
    candles_by_symbol: dict[str, list[dict]],
    min_return_pct: float = 0.05,
    hold_bars: int = 2,
) -> list[dict]:
    """
    Convert OHLCV candles to synthetic trade rows (entry + exit with pnl) for optimizer replay.
    Simple momentum: enter when bar return > min_return_pct, exit after hold_bars.
    Returns list of trade dicts with ts, symbol, side, qty, price, order_id, order_link_id, pnl.
    """
    trades: list[dict] = []
    for symbol, candles in candles_by_symbol.items():
        if len(candles) < hold_bars + 1:
            continue
        idx = 0
        trade_idx = 0
        while idx < len(candles) - hold_bars:
            c0 = candles[idx]
            c1 = candles[idx + 1]
            ret = (c1["close"] - c0["close"]) / c0["close"] * 100 if c0["close"] else 0
            if ret >= min_return_pct:
                entry_ts = c1["start_ts"]
                entry_price = c1["close"]
                exit_idx = min(idx + 1 + hold_bars, len(candles) - 1)
                exit_c = candles[exit_idx]
                exit_ts = exit_c["start_ts"]
                exit_price = exit_c["close"]
                pnl = exit_price - entry_price
                order_id_ent = f"warm_start_ent_{symbol}_{trade_idx}"
                order_id_tp = f"warm_start_tp1_{symbol}_{trade_idx}"
                trades.append({
                    "ts": entry_ts,
                    "symbol": symbol,
                    "side": "Buy",
                    "qty": 1.0,
                    "price": entry_price,
                    "order_id": order_id_ent,
                    "order_link_id": "entry",
                    "pnl": None,
                })
                trades.append({
                    "ts": exit_ts,
                    "symbol": symbol,
                    "side": "Sell",
                    "qty": 1.0,
                    "price": exit_price,
                    "order_id": order_id_tp,
                    "order_link_id": "tp1_1",
                    "pnl": pnl,
                })
                trade_idx += 1
                idx = exit_idx + 1
            elif ret <= -min_return_pct:
                entry_ts = c1["start_ts"]
                entry_price = c1["close"]
                exit_idx = min(idx + 1 + hold_bars, len(candles) - 1)
                exit_c = candles[exit_idx]
                exit_ts = exit_c["start_ts"]
                exit_price = exit_c["close"]
                pnl = entry_price - exit_price
                order_id_ent = f"warm_start_ent_{symbol}_{trade_idx}"
                order_id_tp = f"warm_start_tp1_{symbol}_{trade_idx}"
                trades.append({
                    "ts": entry_ts,
                    "symbol": symbol,
                    "side": "Sell",
                    "qty": 1.0,
                    "price": entry_price,
                    "order_id": order_id_ent,
                    "order_link_id": "entry",
                    "pnl": None,
                })
                trades.append({
                    "ts": exit_ts,
                    "symbol": symbol,
                    "side": "Buy",
                    "qty": 1.0,
                    "price": exit_price,
                    "order_id": order_id_tp,
                    "order_link_id": "tp1_1",
                    "pnl": pnl,
                })
                trade_idx += 1
                idx = exit_idx + 1
            else:
                idx += 1
    return sorted(trades, key=lambda t: (t["ts"], t["symbol"]))


def get_warm_start_candle_cache_path(artifacts_root: str, symbol: str) -> Path:
    """Path for cached candles for one symbol."""
    return Path(artifacts_root) / "warm_start_candles" / f"{symbol}.json"


def load_cached_candles(artifacts_root: str, symbols: list[str]) -> dict[str, list[dict]]:
    """Load candles from local cache if present."""
    out: dict[str, list[dict]] = {}
    for symbol in symbols:
        path = get_warm_start_candle_cache_path(artifacts_root, symbol)
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    out[symbol] = data
                else:
                    out[symbol] = data.get("candles", [])
            except Exception as e:
                log.debug(f"Load cached candles {symbol}: {e}")
    return out


def save_candles_cache(artifacts_root: str, candles_by_symbol: dict[str, list[dict]]) -> None:
    """Persist candles to local cache for reuse."""
    base = Path(artifacts_root) / "warm_start_candles"
    base.mkdir(parents=True, exist_ok=True)
    for symbol, candles in candles_by_symbol.items():
        path = base / f"{symbol}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"symbol": symbol, "candles": candles}, f)
        except Exception as e:
            log.warning(f"Save candle cache {symbol}: {e}")
