"""SQLite persistence for trades, fills, signals, equity."""

import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


class Database:
    """SQLite database for audit trail and backtest."""

    def __init__(self, path: str = "data/bot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                side TEXT,
                qty REAL,
                price REAL,
                order_id TEXT,
                order_link_id TEXT,
                pnl REAL,
                UNIQUE(order_id)
            );
            CREATE TABLE IF NOT EXISTS signal_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                score REAL,
                direction TEXT,
                delta_1m REAL,
                buy_sell_ratio REAL,
                json_features TEXT
            );
            CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                equity REAL,
                pnl_daily REAL
            );
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                module TEXT,
                message TEXT,
                traceback TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
            CREATE INDEX IF NOT EXISTS idx_signals_ts ON signal_snapshots(ts);
            CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_curve(ts);
        """)
        conn.commit()

    def insert_trade(
        self,
        ts: int,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        order_id: str = "",
        order_link_id: str = "",
        pnl: Optional[float] = None,
    ) -> None:
        """Insert trade record."""
        try:
            self._get_conn().execute(
                """INSERT OR IGNORE INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, symbol, side, qty, price, order_id, order_link_id, pnl),
            )
            self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert trade error: {e}")

    def insert_signal(
        self,
        ts: int,
        symbol: str,
        score: float,
        direction: str,
        delta_1m: float,
        buy_sell_ratio: float,
        json_features: str = "",
    ) -> None:
        """Insert signal snapshot."""
        try:
            self._get_conn().execute(
                """INSERT INTO signal_snapshots (ts, symbol, score, direction, delta_1m, buy_sell_ratio, json_features)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, symbol, score, direction, delta_1m, buy_sell_ratio, json_features),
            )
            self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert signal error: {e}")

    def insert_equity(self, ts: int, equity: float, pnl_daily: float = 0) -> None:
        """Insert equity snapshot."""
        try:
            self._get_conn().execute(
                "INSERT INTO equity_curve (ts, equity, pnl_daily) VALUES (?, ?, ?)",
                (ts, equity, pnl_daily),
            )
            self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert equity error: {e}")

    def insert_error(self, ts: int, module: str, message: str, traceback: str = "") -> None:
        """Insert error log."""
        try:
            self._get_conn().execute(
                "INSERT INTO errors (ts, module, message, traceback) VALUES (?, ?, ?, ?)",
                (ts, module, message, traceback),
            )
            self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert error: {e}")

    def get_trades(self, since_ts: Optional[int] = None) -> list[dict]:
        """Get trades for backtest."""
        conn = self._get_conn()
        if since_ts:
            rows = conn.execute("SELECT * FROM trades WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM trades ORDER BY ts").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        """Close connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
