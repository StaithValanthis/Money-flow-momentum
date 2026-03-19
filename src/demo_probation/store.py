"""Demo probation persistence: lifecycle state per config (Demo-only)."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)

LIFECYCLE_DEMO_PROBATION = "DEMO_PROBATION"
LIFECYCLE_DEMO_PROBATION_PASSED = "DEMO_PROBATION_PASSED"
LIFECYCLE_DEMO_PROBATION_FAILED = "DEMO_PROBATION_FAILED"
LIFECYCLE_ACTIVE_DEMO_BASELINE = "ACTIVE_DEMO_BASELINE"


def _get_db(db_path: str):
    from src.storage.db import Database
    return Database(db_path)


def insert_probation_candidate(config_id: str, db_path: str) -> bool:
    """Record a config as entering Demo probation (historically passable seed)."""
    try:
        db = _get_db(db_path)
        conn = db._get_conn()
        now = int(time.time() * 1000)
        conn.execute(
            """INSERT OR REPLACE INTO demo_probation
               (config_id, lifecycle_state, started_at_ts, updated_at_ts, ended_at_ts, failure_reasons, failure_reason_type, metrics_snapshot, promoted_to_baseline_at_ts)
               VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)""",
            (config_id, LIFECYCLE_DEMO_PROBATION, now, now),
        )
        conn.commit()
        db.close()
        return True
    except Exception as e:
        log.warning("insert_probation_candidate failed: {}", e)
        return False


def get_probation_record(config_id: str, db_path: str) -> Optional[Dict[str, Any]]:
    """Return the demo_probation row for config_id, or None."""
    try:
        db = _get_db(db_path)
        conn = db._get_conn()
        row = conn.execute("SELECT * FROM demo_probation WHERE config_id = ?", (config_id,)).fetchone()
        db.close()
        if row is None:
            return None
        return dict(row)
    except Exception as e:
        log.debug("get_probation_record: {}", e)
        return None


def update_probation_state(
    config_id: str,
    db_path: str,
    lifecycle_state: str,
    failure_reasons: Optional[list[str]] = None,
    metrics_snapshot: Optional[Dict[str, Any]] = None,
    promoted_to_baseline_at_ts: Optional[int] = None,
    failure_reason_type: Optional[str] = None,
) -> bool:
    """Update probation row to passed/failed, optional promotion timestamp, and failure_reason_type."""
    try:
        db = _get_db(db_path)
        conn = db._get_conn()
        now = int(time.time() * 1000)
        reasons_json = json.dumps(failure_reasons) if failure_reasons else None
        metrics_json = json.dumps(metrics_snapshot) if metrics_snapshot else None
        end_ts = now if lifecycle_state in (LIFECYCLE_DEMO_PROBATION_PASSED, LIFECYCLE_DEMO_PROBATION_FAILED) else None
        if failure_reason_type is not None:
            conn.execute(
                """UPDATE demo_probation SET
                   lifecycle_state = ?, updated_at_ts = ?, ended_at_ts = COALESCE(?, ended_at_ts),
                   failure_reasons = COALESCE(?, failure_reasons), failure_reason_type = COALESCE(?, failure_reason_type),
                   metrics_snapshot = COALESCE(?, metrics_snapshot),
                   promoted_to_baseline_at_ts = COALESCE(?, promoted_to_baseline_at_ts)
                   WHERE config_id = ?""",
                (lifecycle_state, now, end_ts, reasons_json, failure_reason_type, metrics_json, promoted_to_baseline_at_ts, config_id),
            )
        else:
            conn.execute(
                """UPDATE demo_probation SET
                   lifecycle_state = ?, updated_at_ts = ?, ended_at_ts = COALESCE(?, ended_at_ts),
                   failure_reasons = COALESCE(?, failure_reasons), metrics_snapshot = COALESCE(?, metrics_snapshot),
                   promoted_to_baseline_at_ts = COALESCE(?, promoted_to_baseline_at_ts)
                   WHERE config_id = ?""",
                (lifecycle_state, now, end_ts, reasons_json, metrics_json, promoted_to_baseline_at_ts, config_id),
            )
        conn.commit()
        db.close()
        return True
    except Exception as e:
        log.warning("update_probation_state failed: {}", e)
        return False


def get_current_probation_status(db_path: str) -> Optional[Dict[str, Any]]:
    """Return probation record for the current active config if it is in probation, else None."""
    from src.config.versioning import get_active_config_id
    active_id = get_active_config_id(db_path)
    if not active_id:
        return None
    rec = get_probation_record(active_id, db_path)
    if rec and rec.get("lifecycle_state") == LIFECYCLE_DEMO_PROBATION:
        return rec
    return None
