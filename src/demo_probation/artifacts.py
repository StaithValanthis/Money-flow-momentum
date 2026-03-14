"""Write Demo probation status artifact (Demo-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def write_probation_status_artifact(
    artifacts_root: str,
    instance_name: str,
    payload: Dict[str, Any],
) -> Optional[Path]:
    """Write artifacts/<instance>/probation/demo_probation_status.json."""
    try:
        root = Path(artifacts_root)
        dir_path = root / (instance_name or "demo") / "probation"
        dir_path.mkdir(parents=True, exist_ok=True)
        path = dir_path / "demo_probation_status.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return path
    except Exception:
        return None


def build_probation_status_payload(
    config_id: Optional[str],
    lifecycle_state: Optional[str],
    probation_status: str,
    metrics: Optional[Dict[str, Any]],
    reasons: Optional[list],
    started_at_ts: Optional[int],
    updated_at_ts: Optional[int],
    ended_at_ts: Optional[int],
    promoted_to_baseline_at_ts: Optional[int],
    is_active_baseline: bool,
) -> Dict[str, Any]:
    """Build the JSON payload for demo_probation_status.json."""
    return {
        "candidate_config_id": config_id,
        "lifecycle_state": lifecycle_state or "UNKNOWN",
        "probation_status": probation_status,
        "probation_metrics": metrics or {},
        "pass_fail_reasons": reasons or [],
        "started_at_ts": started_at_ts,
        "updated_at_ts": updated_at_ts,
        "ended_at_ts": ended_at_ts,
        "promoted_to_baseline_at_ts": promoted_to_baseline_at_ts,
        "is_active_demo_baseline": is_active_baseline,
    }
