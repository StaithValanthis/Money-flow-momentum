"""Demo probation evaluation: pass/fail/in-progress from real Demo trades and events (Demo-only)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from src.config.config import Config
from src.evaluation.datasets import get_trade_durations_sec
from src.evaluation.metrics import compute_core_metrics
from src.demo_probation.store import (
    LIFECYCLE_ACTIVE_DEMO_BASELINE,
    LIFECYCLE_DEMO_PROBATION,
    LIFECYCLE_DEMO_PROBATION_FAILED,
    LIFECYCLE_DEMO_PROBATION_PASSED,
    get_probation_record,
    update_probation_state,
)
from src.utils.logging import get_logger

log = get_logger(__name__)

PROBATION_STATUS_IN_PROGRESS = "IN_PROGRESS"
PROBATION_STATUS_PASSED = "PASSED"
PROBATION_STATUS_FAILED = "FAILED"

# Ultra-short = duration below this many seconds
ULTRA_SHORT_DURATION_SEC = 60.0


def _consecutive_losses(pnls: List[float]) -> int:
    """Return max number of consecutive non-positive (loss or zero) trades from the end."""
    if not pnls:
        return 0
    n = 0
    for i in range(len(pnls) - 1, -1, -1):
        if (pnls[i] or 0) <= 0:
            n += 1
        else:
            break
    return n


def _stop_out_rate_from_lifecycle(lifecycle_events: List[dict]) -> float:
    """Fraction of lifecycle events that are stop-out style (protection_repair, stop_moved_breakeven, etc.)."""
    if not lifecycle_events:
        return 0.0
    stop_style = {"protection_repair_success", "stop_moved_breakeven", "time_stop"}
    count = sum(1 for e in lifecycle_events if (e.get("event") or "") in stop_style)
    return count / len(lifecycle_events)


def _ultra_short_fraction(trades: List[dict]) -> float:
    """Fraction of closed trades with duration < ULTRA_SHORT_DURATION_SEC."""
    if not trades:
        return 0.0
    durations = get_trade_durations_sec(trades)
    if not durations:
        return 0.0
    short = sum(1 for d in durations if d < ULTRA_SHORT_DURATION_SEC)
    return short / len(durations)


def evaluate_probation(
    db_path: str,
    config: Config,
    config_id: Optional[str] = None,
) -> Tuple[str, str, List[str], Dict[str, Any]]:
    """
    Evaluate current probation candidate using real Demo data.

    Returns:
        (probation_status, lifecycle_state, reasons, metrics_summary)
        probation_status: IN_PROGRESS | PASSED | FAILED
        lifecycle_state: same or DEMO_PROBATION_PASSED / DEMO_PROBATION_FAILED
        reasons: list of pass/fail/in-progress reasons
        metrics_summary: dict with closed_trades, runtime_minutes, profit_factor, etc.
    """
    from src.config.versioning import get_active_config_id
    from src.storage.db import Database

    prob = getattr(config, "demo_probation", None)
    if not prob or not getattr(prob, "enabled", True):
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["probation_disabled"], {}

    cid = config_id or get_active_config_id(db_path)
    if not cid:
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["no_active_config"], {}

    record = get_probation_record(cid, db_path)
    if not record or record.get("lifecycle_state") != LIFECYCLE_DEMO_PROBATION:
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["no_probation_candidate"], {}

    started_ts = int(record.get("started_at_ts") or 0)
    if started_ts <= 0:
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["invalid_start_ts"], {}

    db = Database(db_path)
    trades = db.get_trades(since_ts=started_ts, config_id=cid)
    lifecycle = db.get_lifecycle_events(since_ts=started_ts, config_id=cid)
    kill_events = db.get_kill_switch_events(since_ts=started_ts)
    db.close()

    # Ensure pnl on trades (pairing if needed)
    from src.evaluation.datasets import compute_realized_pnl_by_pairing
    trades = compute_realized_pnl_by_pairing(trades)
    # Only closed trades (exits with pnl or any trade with pnl set)
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        closed = trades  # fallback: use all trades

    now_ms = int(time.time() * 1000)
    runtime_minutes = (now_ms - started_ts) / (60 * 1000) if started_ts else 0

    metrics = compute_core_metrics(closed) if closed else {}
    stop_out_rate = _stop_out_rate_from_lifecycle(lifecycle)
    ultra_short_frac = _ultra_short_fraction(closed) if closed else 0.0
    pnls = [float(t.get("pnl") or 0) for t in closed]
    cons_losses = _consecutive_losses(pnls)

    metrics_summary = {
        "closed_trades": len(closed),
        "runtime_minutes": round(runtime_minutes, 2),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "expectancy": metrics.get("expectancy", 0.0),
        "consecutive_losses": cons_losses,
        "stop_out_rate": stop_out_rate,
        "ultra_short_trade_fraction": ultra_short_frac,
        "kill_switch_events_since_start": len(kill_events),
    }

    reasons: List[str] = []
    fail_reasons: List[str] = []
    min_trades = getattr(prob, "min_closed_trades", 30)
    min_runtime = getattr(prob, "min_runtime_minutes", 60)
    forbid_kill = getattr(prob, "forbid_kill_switch_hit", True)
    max_cons = getattr(prob, "max_consecutive_losses", 5)
    max_stop = getattr(prob, "max_stop_out_rate", 0.50)
    max_ultra = getattr(prob, "max_ultra_short_trade_fraction", 0.25)
    min_pf = getattr(prob, "min_profit_factor", 1.05)
    min_exp = getattr(prob, "min_expectancy", 0.0)

    # Failure conditions (can fail early)
    if forbid_kill and kill_events:
        fail_reasons.append("kill_switch_hit")
    if len(closed) >= min(5, min_trades) and cons_losses >= max_cons:
        fail_reasons.append("max_consecutive_losses_breach")
    if len(closed) >= min_trades and stop_out_rate > max_stop:
        fail_reasons.append("stop_out_rate_too_high")
    if len(closed) >= min_trades and ultra_short_frac > max_ultra:
        fail_reasons.append("ultra_short_trade_fraction_too_high")

    if fail_reasons:
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary

    # Minimum sample / runtime not yet reached
    if len(closed) < min_trades or runtime_minutes < min_runtime:
        reasons.append("sample_or_runtime_not_reached")
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, reasons, metrics_summary

    # Success criteria
    pf = metrics_summary["profit_factor"]
    exp = metrics_summary["expectancy"]
    if pf < min_pf:
        fail_reasons.append("profit_factor_below_minimum")
    if exp < min_exp:
        fail_reasons.append("expectancy_below_minimum")

    if fail_reasons:
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary

    reasons.append("passed")
    return PROBATION_STATUS_PASSED, LIFECYCLE_DEMO_PROBATION_PASSED, reasons, metrics_summary


def apply_probation_result(
    config_id: str,
    db_path: str,
    config: Config,
    probation_status: str,
    lifecycle_state: str,
    reasons: List[str],
    metrics_summary: Dict[str, Any],
) -> bool:
    """Update DB and optionally promote/reject based on config."""
    prob = getattr(config, "demo_probation", None)
    if not prob:
        return False

    now = int(time.time() * 1000)
    promoted_ts = None
    if lifecycle_state == LIFECYCLE_DEMO_PROBATION_PASSED and getattr(prob, "auto_promote_probation_pass_to_active_demo", True):
        promoted_ts = now

    ok = update_probation_state(
        config_id,
        db_path,
        lifecycle_state,
        failure_reasons=reasons if lifecycle_state == LIFECYCLE_DEMO_PROBATION_FAILED else None,
        metrics_snapshot=metrics_summary,
        promoted_to_baseline_at_ts=promoted_ts,
    )
    if not ok:
        return False

    if lifecycle_state == LIFECYCLE_DEMO_PROBATION_FAILED and getattr(prob, "auto_reject_on_failure", True):
        try:
            from src.config.versioning import reject_config_version
            reject_config_version(config_id, db_path)
        except Exception as e:
            log.warning("auto_reject after probation failure: %s", e)

    return True
