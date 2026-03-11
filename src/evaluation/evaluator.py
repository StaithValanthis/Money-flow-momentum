"""Evaluator: load data from DB and compute metrics, write reports."""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from src.storage.db import Database
from src.evaluation.datasets import compute_realized_pnl_by_pairing
from src.evaluation.metrics import (
    compute_core_metrics,
    compute_stratified_metrics,
    compute_score_bucket_metrics,
    compute_diagnostic_metrics,
    compute_stage4_metrics,
    compute_stage5_portfolio_metrics,
    compute_fill_quality_metrics,
)
from src.evaluation.reporting import write_evaluation_artifacts
from src.utils.logging import get_logger

log = get_logger(__name__)


class Evaluator:
    """Load trades/fills/decisions/lifecycle from DB and produce evaluation report."""

    def __init__(self, db_path: str = "data/bot.db"):
        self.db = Database(db_path)

    def run(
        self,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        config_id: Optional[str] = None,
        symbol: Optional[str] = None,
        initial_equity: float = 10_000.0,
        artifact_dir: Optional[Path] = None,
    ) -> dict:
        """
        Run evaluation over the given window. Returns summary dict and writes artifacts.
        """
        run_id = str(uuid.uuid4())[:8]
        trades = self.db.get_trades(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]
        fills = self.db.get_fills(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
        entry_decisions = self.db.get_entry_decisions(
            since_ts=from_ts, to_ts=to_ts, config_id=config_id, symbol=symbol
        )
        lifecycle = self.db.get_lifecycle_events(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
        equity_curve = self.db.get_equity_curve(since_ts=from_ts, to_ts=to_ts)
        signal_snapshots = self.db.get_signal_snapshots(since_ts=from_ts, to_ts=to_ts, config_id=config_id)
        execution_audit = self.db.get_execution_audit(since_ts=from_ts, to_ts=to_ts, config_id=config_id) if hasattr(self.db, "get_execution_audit") else []

        core = compute_core_metrics(trades, equity_curve, initial_equity)
        # If trades have no PnL (e.g. DB written before execPnl fix), derive from fills
        if core.get("total_pnl", 0) == 0 and fills:
            fill_pnls = [float(f.get("closed_pnl") or 0) for f in fills]
            total_from_fills = sum(fill_pnls)
            if total_from_fills != 0:
                wins_f = [p for p in fill_pnls if p > 0]
                losses_f = [p for p in fill_pnls if p < 0]
                n_f = len(wins_f) + len(losses_f)
                core = {
                    **core,
                    "total_pnl": total_from_fills,
                    "realized_pnl": total_from_fills,
                    "return_pct": (total_from_fills / initial_equity) * 100.0 if initial_equity else 0.0,
                    "win_rate": len(wins_f) / n_f if n_f else 0.0,
                    "expectancy": total_from_fills / n_f if n_f else 0.0,
                    "avg_win": (sum(wins_f) / len(wins_f)) if wins_f else 0.0,
                    "avg_loss": (sum(losses_f) / len(losses_f)) if losses_f else 0.0,
                }
        # If still no PnL, compute from entry/exit pairing (tp1/tp2 vs entry rows)
        if core.get("total_pnl", 0) == 0 and trades:
            trades_with_pnl = compute_realized_pnl_by_pairing(trades)
            core_pair = compute_core_metrics(trades_with_pnl, equity_curve, initial_equity)
            if core_pair.get("total_pnl", 0) != 0:
                core = core_pair
                trades = trades_with_pnl
        by_symbol = compute_stratified_metrics(trades, by="symbol")
        by_side = compute_stratified_metrics(trades, by="side")
        by_config = compute_stratified_metrics(trades, by="config_id") if any(t.get("config_id") for t in trades) else {}
        score_buckets = compute_score_bucket_metrics(entry_decisions, trades, n_buckets=10)
        diagnostic = compute_diagnostic_metrics(lifecycle, entry_decisions, fills)
        stage4_metrics = compute_stage4_metrics(lifecycle, signal_snapshots, entry_decisions, trades)
        stage5_portfolio = compute_stage5_portfolio_metrics(entry_decisions, lifecycle)
        fill_quality = compute_fill_quality_metrics(execution_audit) if execution_audit else {}

        summary = {
            "run_id": run_id,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "config_id": config_id,
            "symbol": symbol,
            "core": core,
            "by_symbol": by_symbol,
            "by_side": by_side,
            "by_config": by_config,
            "score_buckets": score_buckets,
            "diagnostic": diagnostic,
            "stage4": stage4_metrics,
            "stage5_portfolio": stage5_portfolio,
            "fill_quality": fill_quality,
            "trade_count": len(trades),
            "created_at": int(time.time() * 1000),
        }

        if artifact_dir is None:
            artifact_dir = Path("artifacts/evaluations")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_path = write_evaluation_artifacts(summary, artifact_dir, run_id)
        summary["report_path"] = str(report_path)

        self.db.insert_evaluation_report(
            run_id=run_id,
            config_id=config_id,
            from_ts=from_ts,
            to_ts=to_ts,
            summary_json=json.dumps(summary, default=str),
            report_path=str(report_path),
        )
        self.db.close()
        return summary
