"""Demo probation evaluation: pass/fail/in-progress from real Demo trades and events (Demo-only)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from src.config.config import Config
from src.evaluation.datasets import get_trade_durations_sec
from src.evaluation.metrics import compute_core_metrics
from src.lifecycle.logger import append_demo_lifecycle_event
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

# Failure reason types for reporting (timer vs fail-fast)
FAILURE_REASON_TIMER_EVALUATED = "timer_evaluated"
FAILURE_REASON_FAIL_FAST_KILL_SWITCH = "fail_fast_kill_switch"
FAILURE_REASON_FAIL_FAST_HARD_BLOCK = "fail_fast_hard_block"
FAILURE_REASON_FAIL_FAST_CONSECUTIVE_LOSSES = "fail_fast_consecutive_losses"
FAILURE_REASON_FAIL_FAST_STALLED_POOR_METRICS = "fail_fast_stalled_poor_metrics"

# Ultra-short = duration below this many seconds
ULTRA_SHORT_DURATION_SEC = 60.0


def _probation_composite_survival(
    closed: List[dict],
    pf: float,
    exp: float,
    cons_losses: int,
    stop_out_rate: float,
    ultra_short_frac: float,
    prob: Any,
    min_trades: int,
) -> Tuple[float, Dict[str, float], str]:
    """
    Weighted 0–100 score from multiple probation dimensions (heuristic, not optimized).
    primary_failure_driver names the weakest component for attribution.
    """
    max_cons = max(int(getattr(prob, "max_consecutive_losses", 5) or 5), 1)
    max_stop = max(float(getattr(prob, "max_stop_out_rate", 0.50) or 0.5), 0.05)
    max_ultra = max(float(getattr(prob, "max_ultra_short_trade_fraction", 0.25) or 0.25), 0.05)
    n = len(closed)

    def clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
        return max(lo, min(hi, x))

    pf_s = clip((pf - 0.88) / 0.32 * 100)
    exp_s = clip(50.0 + exp * 400.0)
    sor_s = clip((1.0 - stop_out_rate / max_stop) * 100.0)
    cl_s = clip((1.0 - cons_losses / max_cons) * 100.0)
    tr_s = clip(min(1.0, n / max(min_trades, 1)) * 100.0)
    us_s = clip((1.0 - ultra_short_frac / max_ultra) * 100.0)
    durs = get_trade_durations_sec(closed) if closed else []
    if durs:
        med = sorted(durs)[len(durs) // 2]
        dur_s = clip(min(100.0, med / 180.0 * 80.0))
    else:
        dur_s = 55.0

    weights = {
        "profit_factor": 0.20,
        "expectancy": 0.18,
        "stop_out_rate": 0.14,
        "consecutive_losses": 0.14,
        "trade_sample": 0.12,
        "ultra_short_churn": 0.12,
        "duration_health": 0.10,
    }
    comp = {
        "profit_factor": pf_s,
        "expectancy": exp_s,
        "stop_out_rate": sor_s,
        "consecutive_losses": cl_s,
        "trade_sample": tr_s,
        "ultra_short_churn": us_s,
        "duration_health": dur_s,
    }
    score = sum(weights[k] * comp[k] for k in weights)
    worst = min(weights.keys(), key=lambda k: comp[k])
    return round(score, 2), {k: round(comp[k], 2) for k in comp}, worst


def _probation_attribution_from_driver(driver: str) -> str:
    m = {
        "profit_factor": "likely_poor_edge",
        "expectancy": "likely_poor_edge",
        "stop_out_rate": "likely_too_tight_protection",
        "consecutive_losses": "likely_poor_edge",
        "trade_sample": "mixed_or_unclear",
        "ultra_short_churn": "likely_too_tight_protection",
        "duration_health": "likely_too_tight_protection",
    }
    return m.get(driver, "mixed_or_unclear")


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
) -> Tuple[str, str, List[str], Dict[str, Any], Optional[str]]:
    """
    Evaluate current probation candidate using real Demo data (includes fail-fast checks).

    Returns:
        (probation_status, lifecycle_state, reasons, metrics_summary, failure_reason_type)
        failure_reason_type: timer_evaluated | fail_fast_kill_switch | fail_fast_hard_block |
            fail_fast_consecutive_losses | fail_fast_stalled_poor_metrics | None (in-progress/pass)
    """
    from src.config.versioning import get_active_config_id
    from src.storage.db import Database

    prob = getattr(config, "demo_probation", None)
    if not prob or not getattr(prob, "enabled", True):
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["probation_disabled"], {}, None

    cid = config_id or get_active_config_id(db_path)
    if not cid:
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["no_active_config"], {}, None

    record = get_probation_record(cid, db_path)
    if not record or record.get("lifecycle_state") != LIFECYCLE_DEMO_PROBATION:
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["no_probation_candidate"], {}, None

    started_ts = int(record.get("started_at_ts") or 0)
    if started_ts <= 0:
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, ["invalid_start_ts"], {}, None

    db = Database(db_path)
    trades = db.get_trades(since_ts=started_ts, config_id=cid)
    lifecycle = db.get_lifecycle_events(since_ts=started_ts, config_id=cid)
    kill_events = db.get_kill_switch_events(since_ts=started_ts)
    auto_state = db.get_automation_state()
    burnin_breaches = db.get_burnin_gate_breaches(since_ts=started_ts, config_id=cid)
    db.close()

    # Ensure pnl on trades (pairing if needed)
    from src.evaluation.datasets import compute_realized_pnl_by_pairing
    trades = compute_realized_pnl_by_pairing(trades)
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        closed = trades

    now_ms = int(time.time() * 1000)
    runtime_minutes = (now_ms - started_ts) / (60 * 1000) if started_ts else 0

    metrics = compute_core_metrics(closed) if closed else {}
    stop_out_rate = _stop_out_rate_from_lifecycle(lifecycle)
    ultra_short_frac = _ultra_short_fraction(closed) if closed else 0.0
    pnls = [float(t.get("pnl") or 0) for t in closed]
    cons_losses = _consecutive_losses(pnls)
    pf = metrics.get("profit_factor", 0.0)
    exp = metrics.get("expectancy", 0.0)

    metrics_summary = {
        "closed_trades": len(closed),
        "runtime_minutes": round(runtime_minutes, 2),
        "profit_factor": pf,
        "expectancy": exp,
        "consecutive_losses": cons_losses,
        "stop_out_rate": stop_out_rate,
        "ultra_short_trade_fraction": ultra_short_frac,
        "kill_switch_events_since_start": len(kill_events),
    }

    reasons: List[str] = []
    fail_reasons: List[str] = []
    failure_reason_type: Optional[str] = None
    min_trades = getattr(prob, "min_closed_trades", 30)
    min_runtime = getattr(prob, "min_runtime_minutes", 60)
    forbid_kill = getattr(prob, "forbid_kill_switch_hit", True)
    max_cons = getattr(prob, "max_consecutive_losses", 5)
    max_stop = getattr(prob, "max_stop_out_rate", 0.50)
    max_ultra = getattr(prob, "max_ultra_short_trade_fraction", 0.25)
    min_pf = getattr(prob, "min_profit_factor", 1.05)
    min_exp = getattr(prob, "min_expectancy", 0.0)
    fail_fast_kill = getattr(prob, "fail_fast_on_kill_switch", True)
    fail_fast_block = getattr(prob, "fail_fast_on_hard_block", True)
    stall_minutes = getattr(prob, "no_trade_stall_minutes", 10)
    fail_stalled_neg_exp = getattr(prob, "fail_if_stalled_and_negative_expectancy", True)
    fail_stalled_pf_below = getattr(prob, "fail_if_stalled_and_pf_below", 0.90)

    # --- Fail-fast: kill switch ---
    if forbid_kill and kill_events:
        fail_reasons.append("kill_switch_hit")
        failure_reason_type = FAILURE_REASON_FAIL_FAST_KILL_SWITCH if fail_fast_kill else FAILURE_REASON_TIMER_EVALUATED
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, failure_reason_type

    # --- Fail-fast: hard block (burn-in / automation blocked) ---
    auto_state_str = (auto_state or {}).get("state") or ""
    is_blocked = auto_state_str.startswith("BLOCKED_") or (len(burnin_breaches) > 0)
    if is_blocked and fail_fast_block:
        fail_reasons.append("hard_block_burnin_or_automation")
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, FAILURE_REASON_FAIL_FAST_HARD_BLOCK

    # --- Fail-fast: consecutive losses (only when enough trade evidence) ---
    min_trades_cons = getattr(prob, "min_closed_trades_before_consecutive_loss_failure", 8)
    if cons_losses >= max_cons:
        if len(closed) < min_trades_cons:
            reasons.append("consecutive_losses_without_enough_trade_evidence")
            return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, reasons, metrics_summary, None
        fail_reasons.append("max_consecutive_losses_breach")
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, FAILURE_REASON_FAIL_FAST_CONSECUTIVE_LOSSES

    # --- Fail-fast: stall + poor metrics (only when enough trade evidence exists) ---
    min_trades_stall_failure = getattr(prob, "min_closed_trades_before_stall_metric_failure", 5)
    last_trade_ts = max(int(t.get("ts") or 0) for t in closed) if closed else started_ts
    stall_minutes_actual = (now_ms - last_trade_ts) / (60 * 1000)
    if stall_minutes_actual >= stall_minutes:
        if len(closed) < min_trades_stall_failure:
            reasons.append("stall_without_enough_trade_evidence")
            return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, reasons, metrics_summary, None
        poor_exp = fail_stalled_neg_exp and exp < 0
        poor_pf = pf < fail_stalled_pf_below
        if poor_exp or poor_pf:
            fail_reasons.append("stalled_poor_metrics")
            return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, FAILURE_REASON_FAIL_FAST_STALLED_POOR_METRICS

    # --- Other failure conditions (timer-evaluated) ---
    if len(closed) >= min_trades and stop_out_rate > max_stop:
        fail_reasons.append("stop_out_rate_too_high")
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, FAILURE_REASON_TIMER_EVALUATED
    if len(closed) >= min_trades and ultra_short_frac > max_ultra:
        fail_reasons.append("ultra_short_trade_fraction_too_high")
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, FAILURE_REASON_TIMER_EVALUATED

    # Minimum sample / runtime not yet reached
    if len(closed) < min_trades or runtime_minutes < min_runtime:
        reasons.append("sample_or_runtime_not_reached")
        return PROBATION_STATUS_IN_PROGRESS, LIFECYCLE_DEMO_PROBATION, reasons, metrics_summary, None

    # Composite survival (timer path only; does not bypass fail-fast above)
    use_comp = getattr(prob, "use_composite_survival_score", False)
    if use_comp:
        score, components, driver = _probation_composite_survival(
            closed, float(pf or 0), float(exp or 0), cons_losses,
            stop_out_rate, ultra_short_frac, prob, min_trades,
        )
        metrics_summary["probation_survival_score"] = score
        metrics_summary["probation_survival_components"] = components
        metrics_summary["probation_primary_failure_driver"] = driver
        pass_s = float(getattr(prob, "probation_survival_pass_score", 58.0))
        fail_s = float(getattr(prob, "probation_survival_fail_score", 38.0))
        fr_comp = str(getattr(prob, "failure_reason_composite_survival", None) or "composite_survival_fail")
        if score >= pass_s:
            reasons.append("passed_composite_survival")
            metrics_summary["probation_rejection_attribution"] = None
            return PROBATION_STATUS_PASSED, LIFECYCLE_DEMO_PROBATION_PASSED, reasons, metrics_summary, None
        if score < fail_s:
            fail_reasons.append(f"composite_survival_score_{score}_below_{fail_s}")
            metrics_summary["probation_rejection_attribution"] = _probation_attribution_from_driver(driver)
            return (
                PROBATION_STATUS_FAILED,
                LIFECYCLE_DEMO_PROBATION_FAILED,
                fail_reasons,
                metrics_summary,
                fr_comp,
            )

    # Legacy timer success criteria (or gray zone when composite enabled but score in [fail_s, pass_s))
    if pf < min_pf:
        fail_reasons.append("profit_factor_below_minimum")
    if exp < min_exp:
        fail_reasons.append("expectancy_below_minimum")

    if fail_reasons:
        metrics_summary["probation_rejection_attribution"] = "likely_poor_edge"
        return PROBATION_STATUS_FAILED, LIFECYCLE_DEMO_PROBATION_FAILED, fail_reasons, metrics_summary, FAILURE_REASON_TIMER_EVALUATED

    reasons.append("passed")
    metrics_summary["probation_rejection_attribution"] = None
    return PROBATION_STATUS_PASSED, LIFECYCLE_DEMO_PROBATION_PASSED, reasons, metrics_summary, None


def apply_probation_result(
    config_id: str,
    db_path: str,
    config: Config,
    probation_status: str,
    lifecycle_state: str,
    reasons: List[str],
    metrics_summary: Dict[str, Any],
    failure_reason_type: Optional[str] = None,
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
        failure_reason_type=failure_reason_type,
    )
    if not ok:
        return False

    if lifecycle_state == LIFECYCLE_DEMO_PROBATION_FAILED and getattr(prob, "auto_reject_on_failure", True):
        try:
            from src.config.versioning import reject_config_version
            reject_config_version(config_id, db_path)
        except Exception as e:
            log.warning("auto_reject after probation failure: {}", e)

    return True


def run_probation_fail_fast_check(db_path: str, config: Config) -> bool:
    """
    Run probation evaluation (including fail-fast). If candidate fails, mark failed, persist, write artifact.
    Call from trade close / kill switch / lifecycle / heartbeat so failures are detected immediately.
    Returns True if probation was just failed (caller may stop or reinit); False otherwise.
    """
    from src.demo_probation import get_current_probation_status
    from src.demo_probation.artifacts import build_probation_status_payload, write_probation_status_artifact

    prob = getattr(config, "demo_probation", None)
    if not prob or not getattr(prob, "enabled", True):
        return False
    prob_status = get_current_probation_status(db_path)
    if not prob_status:
        return False
    p_status, p_lifecycle, p_reasons, p_metrics, p_failure_type = evaluate_probation(
        db_path, config, config_id=prob_status.get("config_id")
    )
    if p_status != "FAILED":
        return False
    apply_probation_result(
        prob_status["config_id"], db_path, config,
        p_status, p_lifecycle, p_reasons, p_metrics, failure_reason_type=p_failure_type,
    )
    instance = getattr(config, "instance_name", None) or "demo"
    payload = build_probation_status_payload(
        prob_status["config_id"], p_lifecycle, p_status, p_metrics, p_reasons,
        prob_status.get("started_at_ts"), prob_status.get("updated_at_ts"),
        int(time.time() * 1000), None, False, failure_reason_type=p_failure_type,
    )
    write_probation_status_artifact(config.artifacts_root, instance, payload)
    reason_str = "; ".join(p_reasons) if p_reasons else "unknown"
    log.warning(
        "Demo probation fail-fast: failed reason_type={} reasons={}",
        p_failure_type or "timer_evaluated",
        reason_str,
    )
    append_demo_lifecycle_event(
        config.artifacts_root, getattr(config, "instance_name", None),
        "PROBATION", "failed",
        config_id=prob_status["config_id"],
        reason="; ".join(p_reasons) if p_reasons else None,
        failure_reason_type=p_failure_type,
        metrics=p_metrics,
    )
    return True
