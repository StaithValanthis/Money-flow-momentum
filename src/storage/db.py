"""SQLite persistence for trades, fills, signals, equity."""

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from src.utils.logging import get_logger
from src.storage.migrations import run_stage3_migrations

log = get_logger(__name__)


class Database:
    """SQLite database for audit trail and backtest. Thread-safe: connection may be used from main and worker threads under a lock."""

    def __init__(self, path: str = "data/bot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_schema()
        run_stage3_migrations(path)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
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
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                order_id TEXT UNIQUE,
                order_link_id TEXT,
                symbol TEXT,
                side TEXT,
                qty REAL,
                price REAL,
                status TEXT,
                reduce_only INTEGER,
                created_ts INTEGER,
                updated_ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                exec_id TEXT UNIQUE,
                order_id TEXT,
                symbol TEXT,
                side TEXT,
                qty REAL,
                price REAL,
                closed_pnl REAL
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
            CREATE TABLE IF NOT EXISTS entry_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                direction TEXT,
                reason TEXT,
                score REAL,
                dry_run INTEGER
            );
            CREATE TABLE IF NOT EXISTS lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                event TEXT,
                phase TEXT,
                exit_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS kill_switch_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                reason TEXT
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
            CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);
            CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts);
            CREATE INDEX IF NOT EXISTS idx_signals_ts ON signal_snapshots(ts);
            CREATE INDEX IF NOT EXISTS idx_entry_ts ON entry_decisions(ts);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_ts ON lifecycle_events(ts);
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
        config_id: Optional[str] = None,
    ) -> None:
        """Insert trade record."""
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT OR IGNORE INTO trades (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, symbol, side, qty, price, order_id, order_link_id, pnl, config_id),
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
        config_id: Optional[str] = None,
    ) -> None:
        """Insert signal snapshot."""
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO signal_snapshots (ts, symbol, score, direction, delta_1m, buy_sell_ratio, json_features, config_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, symbol, score, direction, delta_1m, buy_sell_ratio, json_features, config_id),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert signal error: {e}")

    def insert_equity(self, ts: int, equity: float, pnl_daily: float = 0, config_id: Optional[str] = None) -> None:
        """Insert equity snapshot."""
        try:
            with self._lock:
                self._get_conn().execute(
                    "INSERT INTO equity_curve (ts, equity, pnl_daily, config_id) VALUES (?, ?, ?, ?)",
                    (ts, equity, pnl_daily, config_id),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert equity error: {e}")

    def insert_error(self, ts: int, module: str, message: str, traceback: str = "") -> None:
        """Insert error log."""
        try:
            with self._lock:
                self._get_conn().execute(
                    "INSERT INTO errors (ts, module, message, traceback) VALUES (?, ?, ?, ?)",
                    (ts, module, message, traceback),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert error: {e}")

    def insert_entry_decision(
        self,
        ts: int,
        symbol: str,
        direction: str,
        reason: str,
        score: float = 0,
        dry_run: bool = False,
        config_id: Optional[str] = None,
    ) -> None:
        """Log entry decision or rejection."""
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO entry_decisions (ts, symbol, direction, reason, score, dry_run, config_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ts, symbol, direction, reason, score, 1 if dry_run else 0, config_id),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert entry_decision: {e}")

    def insert_lifecycle_event(self, ts: int, symbol: str, event: str, phase: str = "", exit_reason: str = "", config_id: Optional[str] = None) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    "INSERT INTO lifecycle_events (ts, symbol, event, phase, exit_reason, config_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, symbol, event, phase, exit_reason, config_id),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error("DB insert lifecycle: {}", e)

    def insert_kill_switch(self, ts: int, reason: str) -> None:
        try:
            with self._lock:
                self._get_conn().execute("INSERT INTO kill_switch_events (ts, reason) VALUES (?, ?)", (ts, reason))
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert kill_switch: {e}")

    def get_kill_switch_events(
        self,
        since_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> list[dict]:
        """Return kill-switch events in [since_ts, to_ts] as list of dicts with ts, reason."""
        try:
            conn = self._get_conn()
            sql = "SELECT ts, reason FROM kill_switch_events WHERE 1=1"
            params: list = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            sql += " ORDER BY ts ASC"
            rows = conn.execute(sql, params).fetchall()
            return [{"ts": r[0], "reason": r[1] or ""} for r in rows]
        except Exception as e:
            log.debug(f"get_kill_switch_events: {e}")
            return []

    # --- Automation / orchestration state ---

    def get_automation_state(self) -> dict[str, Any]:
        """Return latest automation_state row as dict, or {} if none."""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT * FROM automation_state WHERE id = 1").fetchone()
            return dict(row) if row else {}
        except Exception as e:
            log.debug(f"get_automation_state: {e}")
            return {}

    def upsert_automation_state(self, state: dict[str, Any]) -> None:
        """Insert or replace automation_state row (id=1)."""
        fields = [
            "state",
            "last_readiness_ts",
            "last_readiness_classification",
            "last_evaluation_run_id",
            "last_evaluation_ts",
            "last_optimizer_run_id",
            "last_optimizer_ts",
            "best_candidate_config_id",
            "shadow_candidate_config_id",
            "last_recommendation_status",
            "blocked_reason",
            "last_error",
            "updated_ts",
        ]
        values = [state.get(f) for f in fields]
        placeholders = ",".join("?" for _ in fields)
        try:
            with self._lock:
                self._get_conn().execute(
                    f"INSERT OR REPLACE INTO automation_state (id, {', '.join(fields)}) VALUES (1, {placeholders})",
                    values,
                )
                self._get_conn().commit()
        except Exception as e:
            log.debug(f"upsert_automation_state: {e}")

    def insert_fill(
        self,
        ts: int,
        exec_id: str,
        order_id: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        closed_pnl: float = 0.0,
        config_id: Optional[str] = None,
    ) -> None:
        """Insert fill record (used for TP1/TP2 logging)."""
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT OR IGNORE INTO fills
                       (ts, exec_id, order_id, symbol, side, qty, price, closed_pnl, config_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, exec_id, order_id, symbol, side, qty, price, closed_pnl, config_id),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert fill error: {e}")

    def get_trades(self, since_ts: Optional[int] = None, to_ts: Optional[int] = None, config_id: Optional[str] = None) -> list[dict]:
        """Get trades for backtest/evaluation."""
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM trades WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            sql += " ORDER BY ts"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_fills(self, since_ts: Optional[int] = None, to_ts: Optional[int] = None, config_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM fills WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_entry_decisions(self, since_ts: Optional[int] = None, to_ts: Optional[int] = None, config_id: Optional[str] = None, symbol: Optional[str] = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM entry_decisions WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            if symbol is not None:
                sql += " AND symbol = ?"
                params.append(symbol)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_lifecycle_events(self, since_ts: Optional[int] = None, to_ts: Optional[int] = None, config_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM lifecycle_events WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_signal_snapshots(self, since_ts: Optional[int] = None, to_ts: Optional[int] = None, config_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM signal_snapshots WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_equity_curve(self, since_ts: Optional[int] = None, to_ts: Optional[int] = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM equity_curve WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def insert_evaluation_report(
        self,
        run_id: str,
        config_id: Optional[str],
        from_ts: Optional[int],
        to_ts: Optional[int],
        summary_json: str,
        report_path: Optional[str] = None,
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO evaluation_reports (run_id, config_id, from_ts, to_ts, created_at, summary_json, report_path)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, config_id, from_ts, to_ts, int(__import__("time").time() * 1000), summary_json, report_path),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert evaluation_report: {e}")

    def insert_optimization_run(
        self,
        run_id: str,
        baseline_config_id: Optional[str],
        from_ts: Optional[int],
        to_ts: Optional[int],
        status: str,
        summary_json: str = "",
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO optimization_runs (run_id, baseline_config_id, created_at, from_ts, to_ts, status, summary_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, baseline_config_id, int(__import__("time").time() * 1000), from_ts, to_ts, status, summary_json),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert optimization_run: {e}")

    def insert_optimization_result(
        self,
        run_id: str,
        config_id: Optional[str],
        segment_name: str,
        in_sample: int,
        metrics_json: str,
        params_json: str,
        pass_fail: int,
        reason_codes: str = "",
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO optimization_results (run_id, config_id, segment_name, in_sample, metrics_json, params_json, pass_fail, reason_codes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, config_id, segment_name, in_sample, metrics_json, params_json, pass_fail, reason_codes),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert optimization_result: {e}")

    def insert_degradation_event(
        self,
        config_id: str,
        severity: str,
        metric: str,
        value: float,
        threshold: float,
        message: str,
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO degradation_events (config_id, ts, severity, metric, value, threshold, message)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (config_id, int(__import__("time").time() * 1000), severity, metric, value, threshold, message),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert degradation: {e}")

    def insert_execution_audit(
        self,
        ts: int,
        symbol: str,
        side: str,
        intent_qty: float,
        intent_price: Optional[float] = None,
        intent_stop: Optional[float] = None,
        order_id: Optional[str] = None,
        order_link_id: Optional[str] = None,
        ack_ts: Optional[int] = None,
        fill_qty: Optional[float] = None,
        fill_price: Optional[float] = None,
        fill_ts: Optional[int] = None,
        slippage_bps: Optional[float] = None,
        size_delta: Optional[float] = None,
        notional_delta: Optional[float] = None,
        mismatch_reason: Optional[str] = None,
        config_id: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO execution_audit
                       (ts, symbol, side, intent_qty, intent_price, intent_stop, order_id, order_link_id, ack_ts,
                        fill_qty, fill_price, fill_ts, slippage_bps, size_delta, notional_delta, mismatch_reason, config_id, strategy)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, symbol, side, intent_qty, intent_price, intent_stop, order_id, order_link_id, ack_ts,
                     fill_qty, fill_price, fill_ts, slippage_bps, size_delta, notional_delta, mismatch_reason, config_id, strategy),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert execution_audit: {e}")

    def insert_protection_audit(
        self,
        ts: int,
        symbol: str,
        check_type: str,
        expected_value: Optional[float] = None,
        actual_value: Optional[float] = None,
        repaired: bool = False,
        message: Optional[str] = None,
        config_id: Optional[str] = None,
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO protection_audit (ts, symbol, check_type, expected_value, actual_value, repaired, message, config_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, symbol, check_type, expected_value, actual_value, 1 if repaired else 0, message, config_id),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert protection_audit: {e}")

    def insert_burnin_gate_breach(
        self,
        ts: int,
        gate_name: str,
        value: Optional[float] = None,
        limit_value: Optional[float] = None,
        message: Optional[str] = None,
        config_id: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> None:
        try:
            with self._lock:
                self._get_conn().execute(
                    """INSERT INTO burnin_gate_breaches (ts, gate_name, value, limit_value, message, config_id, phase)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ts, gate_name, value, limit_value, message, config_id, phase),
                )
                self._get_conn().commit()
        except Exception as e:
            log.error(f"DB insert burnin_gate_breach: {e}")

    def update_execution_audit_on_fill(
        self,
        order_id: str,
        fill_qty: float,
        fill_price: float,
        fill_ts: int,
        slippage_bps: Optional[float] = None,
        size_delta: Optional[float] = None,
        notional_delta: Optional[float] = None,
        mismatch_reason: Optional[str] = None,
    ) -> None:
        try:
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    """UPDATE execution_audit SET fill_qty = ?, fill_price = ?, fill_ts = ?, slippage_bps = ?, size_delta = ?, notional_delta = ?, mismatch_reason = ?
                       WHERE order_id = ?""",
                    (fill_qty, fill_price, fill_ts, slippage_bps, size_delta, notional_delta, mismatch_reason, order_id),
                )
                conn.commit()
        except Exception as e:
            log.error(f"DB update execution_audit fill: {e}")

    def get_execution_audit(
        self,
        since_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        config_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM execution_audit WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            if order_id is not None:
                sql += " AND order_id = ?"
                params.append(order_id)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_protection_audit(
        self,
        since_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        config_id: Optional[str] = None,
    ) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM protection_audit WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_burnin_gate_breaches(
        self,
        since_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        config_id: Optional[str] = None,
    ) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM burnin_gate_breaches WHERE 1=1"
            params: list[Any] = []
            if since_ts is not None:
                sql += " AND ts >= ?"
                params.append(since_ts)
            if to_ts is not None:
                sql += " AND ts <= ?"
                params.append(to_ts)
            if config_id is not None:
                sql += " AND (config_id = ? OR config_id IS NULL)"
                params.append(config_id)
            sql += " ORDER BY ts"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        """Close connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
