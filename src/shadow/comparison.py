"""Compare baseline vs shadow candidate: overlap, decision diff, frequency."""

import json
from pathlib import Path
from typing import Any, Optional

from src.storage.db import Database
from src.utils.logging import get_logger

log = get_logger(__name__)


def compare_baseline_shadow(
    shadow_run_id: int,
    db_path: str = "data/bot.db",
    artifact_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Load shadow_decisions for run, compute overlap with baseline decision, score diff, frequency.
    """
    db = Database(db_path)
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT * FROM shadow_decisions WHERE shadow_run_id = ? ORDER BY ts",
        (shadow_run_id,),
    ).fetchall()
    decisions = [dict(r) for r in rows]
    run_row = conn.execute("SELECT * FROM shadow_runs WHERE id = ?", (shadow_run_id,)).fetchone()
    run = dict(run_row) if run_row else {}
    db.close()

    total = len(decisions)
    same_decision = sum(1 for d in decisions if d.get("direction") == d.get("baseline_decision"))
    score_diffs = [float(d.get("score") or 0) - float(d.get("baseline_score") or 0) for d in decisions]
    avg_score_diff = sum(score_diffs) / len(score_diffs) if score_diffs else 0.0

    out = {
        "shadow_run_id": shadow_run_id,
        "candidate_config_id": run.get("candidate_config_id"),
        "baseline_config_id": run.get("baseline_config_id"),
        "mode": "post_hoc",
        "decision_count": total,
        "same_decision_count": same_decision,
        "agreement_rate": same_decision / total if total else 0.0,
        "avg_score_diff": avg_score_diff,
        "decisions_sample": decisions[:50],
    }

    if artifact_dir is None:
        artifact_dir = Path("artifacts/shadow")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"shadow_comparison_{shadow_run_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    out["report_path"] = str(path)

    return out
