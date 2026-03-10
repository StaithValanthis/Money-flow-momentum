"""Evaluator: load data from DB and compute metrics, write reports."""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from src.storage.db import Database
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
