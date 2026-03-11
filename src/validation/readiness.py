"""Burn-in readiness: aggregate validation state and classify readiness."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)

READINESS_NOT_READY = "NOT_READY"
READINESS_READY_TESTNET = "READY_FOR_TESTNET_CONTINUATION"
READINESS_READY_SMALL_LIVE = "READY_FOR_SMALL_LIVE"
READINESS_NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class ReadinessResult:
    classification: str
    details: dict[str, Any] = field(default_factory=dict)
    message: str = ""


def compute_readiness(
    db,
    *,
    heartbeat_path: Optional[Path] = None,
    config_id: Optional[str] = None,
    window_hours: float = 24.0,
    burn_in_phase: str = "testnet",
) -> ReadinessResult:
    """
    Compute burn-in readiness from DB and optional heartbeat.
    Returns classification: NOT_READY, READY_FOR_TESTNET_CONTINUATION, READY_FOR_SMALL_LIVE, NEEDS_REVIEW.
    """
    if db is None:
        return ReadinessResult(READINESS_NOT_READY, {}, "No database")

    now_ms = int(time.time() * 1000)
    from_ts = now_ms - int(window_hours * 3600 * 1000)
    details: dict[str, Any] = {
        "window_hours": window_hours,
        "from_ts": from_ts,
        "to_ts": now_ms,
        "config_id": config_id,
        "phase": burn_in_phase,
    }

    # Protection audit: count mismatches (non-repaired)
    try:
        prot = db.get_protection_audit(since_ts=from_ts, to_ts=now_ms, config_id=config_id)
        mismatch_count = sum(1 for p in prot if not p.get("repaired"))
        details["protection_mismatch_count"] = mismatch_count
    except Exception as e:
        log.debug(f"Readiness protection_audit: {e}")
        details["protection_mismatch_count"] = 0

    # Execution drift: audit rows with mismatch_reason
    try:
        exec_audit = db.get_execution_audit(since_ts=from_ts, to_ts=now_ms, config_id=config_id)
        drift_count = sum(1 for e in exec_audit if e.get("mismatch_reason"))
        details["execution_drift_count"] = drift_count
        details["execution_audit_count"] = len(exec_audit)
    except Exception as e:
        log.debug(f"Readiness execution_audit: {e}")
        details["execution_drift_count"] = 0
        details["execution_audit_count"] = 0

    # Burn-in gate breaches
    try:
        breaches = db.get_burnin_gate_breaches(since_ts=from_ts, to_ts=now_ms, config_id=config_id)
        details["burnin_gate_breach_count"] = len(breaches)
    except Exception as e:
        log.debug(f"Readiness burnin_breaches: {e}")
        details["burnin_gate_breach_count"] = 0

    # Kill switch
    try:
        conn = db._get_conn()
        kill = conn.execute("SELECT COUNT(*) FROM kill_switch_events WHERE ts >= ?", (from_ts,)).fetchone()[0]
        details["kill_switch_count"] = kill
    except Exception as e:
        details["kill_switch_count"] = 0

    # Degradation events
    try:
        conn = db._get_conn()
        deg = conn.execute("SELECT COUNT(*) FROM degradation_events WHERE ts >= ?", (from_ts,)).fetchone()[0]
        details["degradation_event_count"] = deg
    except Exception as e:
        details["degradation_event_count"] = 0

    # Heartbeat coverage (if file present)
    heartbeat_coverage = None
    if heartbeat_path and heartbeat_path.exists():
        try:
            from src.monitoring.heartbeat import read_heartbeat
            data = read_heartbeat(heartbeat_path)
            if data and data.get("loops"):
                ts = data.get("ts", 0)
                age_sec = time.time() - ts if ts else 999999
                # Simple: if heartbeat < 5 min old, consider coverage 1.0; else 0
                heartbeat_coverage = 1.0 if age_sec < 300 else max(0, 1.0 - (age_sec - 300) / 3600)
                details["heartbeat_age_sec"] = age_sec
                details["heartbeat_coverage"] = heartbeat_coverage
        except Exception as e:
            log.debug(f"Readiness heartbeat: {e}")
    else:
        details["heartbeat_coverage"] = None
        details["heartbeat_age_sec"] = None

    # Trade count in window
    try:
        trades = db.get_trades(since_ts=from_ts, to_ts=now_ms, config_id=config_id)
        details["trade_count"] = len(trades)
    except Exception as e:
        details["trade_count"] = 0

    # Classify
    if details.get("kill_switch_count", 0) > 0:
        return ReadinessResult(READINESS_NOT_READY, details, "Kill switch triggered in window")
    if details.get("burnin_gate_breach_count", 0) > 0:
        return ReadinessResult(READINESS_NEEDS_REVIEW, details, "Burn-in gate breach(es) in window")
    if details.get("protection_mismatch_count", 0) > 0:
        return ReadinessResult(READINESS_NEEDS_REVIEW, details, "Protection mismatch(es) in window")
    if details.get("execution_drift_count", 0) > 0:
        return ReadinessResult(READINESS_NEEDS_REVIEW, details, "Execution drift in window")
    if details.get("degradation_event_count", 0) > 0:
        return ReadinessResult(READINESS_NEEDS_REVIEW, details, "Degradation event(s) in window")

    if burn_in_phase == "testnet":
        return ReadinessResult(READINESS_READY_TESTNET, details, "OK for testnet continuation")
    if burn_in_phase == "demo":
        return ReadinessResult(READINESS_READY_TESTNET, details, "OK for demo continuation")
    if burn_in_phase == "live_small":
        return ReadinessResult(READINESS_READY_SMALL_LIVE, details, "OK for small live (review metrics)")
    return ReadinessResult(READINESS_NEEDS_REVIEW, details, "Review before proceeding")
