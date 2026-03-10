"""Promote candidate to active (manual by default)."""

from typing import Optional

from src.config.versioning import activate_config_version, get_config_version, stage_config_version
from src.promotion.rules import check_promotion_eligibility
from src.evaluation.metrics import compute_core_metrics
from src.storage.db import Database
from src.utils.logging import get_logger

log = get_logger(__name__)


def promote_candidate(
    config_id: str,
    db_path: str = "data/bot.db",
    auto_approved: bool = False,
    baseline_metrics: Optional[dict] = None,
    candidate_metrics: Optional[dict] = None,
    shadow_decision_count: int = 0,
) -> tuple[bool, str]:
    """
    Promote config to active. If not auto_approved, eligibility is checked but
    manual promotion is still required (activate_config_version). Returns (success, message).
    """
    rec = get_config_version(config_id, db_path)
    if not rec:
        return False, "config not found"
    if rec.get("status") == "active":
        return True, "already active"

    eligible, reasons = check_promotion_eligibility(
        candidate_metrics or {},
        baseline_metrics=baseline_metrics,
        shadow_decision_count=shadow_decision_count,
    )
    if not eligible and not auto_approved:
        return False, f"not eligible: {reasons}"

    ok = activate_config_version(config_id, db_path)
    return ok, "promoted" if ok else "activate failed"
