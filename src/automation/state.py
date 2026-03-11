"""Automation state model for Demo orchestration."""

import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


# High-level orchestration states
STATE_IDLE = "IDLE"
STATE_WAITING_FOR_BURNIN_DATA = "WAITING_FOR_BURNIN_DATA"
STATE_READY_FOR_EVALUATION = "READY_FOR_EVALUATION"
STATE_EVALUATING = "EVALUATING"
STATE_READY_FOR_OPTIMIZATION = "READY_FOR_OPTIMIZATION"
STATE_OPTIMIZING = "OPTIMIZING"
STATE_CANDIDATE_AVAILABLE = "CANDIDATE_AVAILABLE"
STATE_CONTINUE_DEMO_NO_CANDIDATE = "CONTINUE_DEMO_NO_CANDIDATE"
STATE_SHADOW_RUNNING = "SHADOW_RUNNING"
STATE_AWAITING_MANUAL_APPROVAL = "AWAITING_MANUAL_APPROVAL"
STATE_DEMO_AUTO_ADOPTED = "DEMO_AUTO_ADOPTED"
STATE_BLOCKED_BY_BURNIN = "BLOCKED_BY_BURNIN"
STATE_BLOCKED_BY_KILL_SWITCH = "BLOCKED_BY_KILL_SWITCH"
STATE_BLOCKED_BY_HEALTH = "BLOCKED_BY_HEALTH"
STATE_ERROR = "ERROR"


RECOMMENDATION_NOT_READY = "NOT_READY"
RECOMMENDATION_CONTINUE_DEMO = "CONTINUE_DEMO"
RECOMMENDATION_READY_FOR_CONFIG_REVIEW = "READY_FOR_CONFIG_REVIEW"
RECOMMENDATION_READY_FOR_LIVE_REVIEW = "READY_FOR_LIVE_REVIEW"
RECOMMENDATION_DEMO_AUTO_ADOPTED = "DEMO_AUTO_ADOPTED"


@dataclass
class AutomationSnapshot:
    """In-memory representation of automation_state row."""

    state: str = STATE_IDLE
    last_readiness_ts: Optional[int] = None
    last_readiness_classification: Optional[str] = None
    last_evaluation_run_id: Optional[str] = None
    last_evaluation_ts: Optional[int] = None
    last_optimizer_run_id: Optional[str] = None
    last_optimizer_ts: Optional[int] = None
    best_candidate_config_id: Optional[str] = None
    shadow_candidate_config_id: Optional[str] = None
    last_recommendation_status: Optional[str] = None
    blocked_reason: Optional[str] = None
    last_error: Optional[str] = None
    updated_ts: int = 0
    last_demo_adoption_ts: Optional[int] = None

    @classmethod
    def from_db(cls, row: dict[str, Any] | None) -> "AutomationSnapshot":
        if not row:
            return cls(updated_ts=int(time.time() * 1000))
        data = dict(row)
        return cls(
            state=data.get("state", STATE_IDLE),
            last_readiness_ts=data.get("last_readiness_ts"),
            last_readiness_classification=data.get("last_readiness_classification"),
            last_evaluation_run_id=data.get("last_evaluation_run_id"),
            last_evaluation_ts=data.get("last_evaluation_ts"),
            last_optimizer_run_id=data.get("last_optimizer_run_id"),
            last_optimizer_ts=data.get("last_optimizer_ts"),
            best_candidate_config_id=data.get("best_candidate_config_id"),
            shadow_candidate_config_id=data.get("shadow_candidate_config_id"),
            last_recommendation_status=data.get("last_recommendation_status"),
            blocked_reason=data.get("blocked_reason"),
            last_error=data.get("last_error"),
            updated_ts=data.get("updated_ts") or int(time.time() * 1000),
            last_demo_adoption_ts=data.get("last_demo_adoption_ts"),
        )

    def to_db_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not self.updated_ts:
            d["updated_ts"] = int(time.time() * 1000)
        return d


def transition(snapshot: AutomationSnapshot, new_state: str, *, reason: Optional[str] = None) -> AutomationSnapshot:
    """Helper to move to a new state with logging and timestamp."""
    if snapshot.state != new_state:
        log.info(f"Automation state transition: {snapshot.state} -> {new_state} ({reason or ''})")
    snapshot.state = new_state
    if reason:
        snapshot.blocked_reason = reason if new_state.startswith("BLOCKED_") else None
    snapshot.updated_ts = int(time.time() * 1000)
    return snapshot

