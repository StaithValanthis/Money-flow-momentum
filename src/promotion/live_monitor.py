"""Live degradation monitoring: compare active config to expected band, persist events."""

from typing import Any, Optional

from src.storage.db import Database
from src.config.versioning import get_active_config_id
from src.evaluation.metrics import compute_core_metrics, compute_diagnostic_metrics
from src.evaluation.datasets import load_evaluation_dataset
from src.utils.logging import get_logger

log = get_logger(__name__)

INSUFFICIENT_DATA_TRADE_COUNT = 5


class LiveDegradationMonitor:
    """
    Monitor active config: drawdown breach, expectancy collapse, stop-out clustering.
    Persist degradation_events; support warning / degraded / rollback recommendation.
    Requires at least min_trade_count_per_period trades in the evaluation window to evaluate.
    """

    def __init__(
        self,
        db_path: str = "data/bot.db",
        max_drawdown_pct: float = 10.0,
        min_expectancy: float = -10.0,
        max_stop_out_rate: float = 0.3,
        min_trade_count_per_period: int = 5,
    ):
        self.db = Database(db_path)
        self.max_drawdown_pct = max_drawdown_pct
        self.min_expectancy = min_expectancy
        self.max_stop_out_rate = max_stop_out_rate
        self.min_trade_count = min_trade_count_per_period

    def check(
        self,
        metrics: dict[str, Any],
        config_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Check metrics against thresholds. Persist degradation_events. Return list of events.
        Skips persistence if trade_count < min_trade_count (insufficient data).
        """
        cid = config_id or get_active_config_id(self.db.path)
        if not cid:
            return []
        if int(metrics.get("trade_count") or 0) < self.min_trade_count:
            log.debug("Degradation check skipped: insufficient data")
            return []
        events = []
        dd = float(metrics.get("max_drawdown") or 0)
        if dd > self.max_drawdown_pct:
            self.db.insert_degradation_event(
                cid, "warning", "max_drawdown", dd, self.max_drawdown_pct,
                f"Drawdown {dd:.1f}% exceeds {self.max_drawdown_pct}%",
            )
            events.append({"severity": "warning", "metric": "max_drawdown", "value": dd})
        exp = float(metrics.get("expectancy") or 0)
        if exp < self.min_expectancy:
            self.db.insert_degradation_event(
                cid, "degraded", "expectancy", exp, self.min_expectancy,
                f"Expectancy {exp:.2f} below {self.min_expectancy}",
            )
            events.append({"severity": "degraded", "metric": "expectancy", "value": exp})
        diag = metrics.get("diagnostic") or {}
        stop_out = float(diag.get("stop_out_rate") or 0)
        if stop_out > self.max_stop_out_rate:
            self.db.insert_degradation_event(
                cid, "warning", "stop_out_rate", stop_out, self.max_stop_out_rate,
                f"Stop-out rate {stop_out:.1%} exceeds {self.max_stop_out_rate:.1%}",
            )
            events.append({"severity": "warning", "metric": "stop_out_rate", "value": stop_out})
        return events

    def check_from_db(
        self,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        config_id: Optional[str] = None,
    ) -> tuple[list[dict], str]:
        """
        Load recent data from DB, compute metrics, run check. Returns (events, status).
        status is "ok" | "insufficient_data" | "no_active_config" | "degradation_detected".
        """
        cid = config_id or get_active_config_id(self.db.path)
        if not cid:
            return [], "no_active_config"
        data = load_evaluation_dataset(
            self.db.path,
            from_ts=from_ts,
            to_ts=to_ts,
            config_id=cid,
        )
        trades = data["trades"]
        if len(trades) < self.min_trade_count:
            return [], "insufficient_data"
        core = compute_core_metrics(trades)
        core["diagnostic"] = compute_diagnostic_metrics(
            data["lifecycle_events"], data["entry_decisions"], data["fills"]
        )
        events = self.check(core, config_id=cid)
        return events, "degradation_detected" if events else "ok"
