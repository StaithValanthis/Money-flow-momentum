"""Demo-only research verdict and strategy viability policy.

This module aggregates cumulative Demo evidence (warm-start + Demo research)
and classifies the current state of the strategy for operator review.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Tuple

from src.automation.state import AutomationSnapshot
from src.config.config import Config
from src.evaluation.datasets import get_trade_durations_sec
from src.evaluation.metrics import compute_core_metrics
from src.storage.db import Database
from src.utils.logging import get_logger

log = get_logger(__name__)

# Verdict labels (Demo-only)
VERDICT_SEARCH_NOT_MATURE = "SEARCH_NOT_MATURE"
VERDICT_DEMO_VALIDATION_NOT_MATURE = "DEMO_VALIDATION_NOT_MATURE"
VERDICT_CANDIDATE_READY_FOR_REVIEW = "CANDIDATE_READY_FOR_REVIEW"
VERDICT_STRATEGY_EDGE_DOUBTFUL = "STRATEGY_EDGE_DOUBTFUL"
VERDICT_DISABLED = "VERDICT_DISABLED"


def _load_automation_snapshot(db: Database) -> AutomationSnapshot:
    """Load current AutomationSnapshot from DB (Demo orchestration only)."""
    row = db.get_automation_state()
    return AutomationSnapshot.from_db(row or None)


def collect_research_evidence(config: Config, db: Database) -> Dict[str, Any]:
    """
    Collect cumulative Demo research evidence for verdict computation.

    Demo-only: assumes config/database_path point to Demo instance.
    """
    artifacts_root = Path(config.artifacts_root)

    # Warm-start evidence from latest warm_start_report.json (if any)
    warm_report_path = artifacts_root / "warm_start" / "warm_start_report.json"
    warm: Dict[str, Any] = {}
    if warm_report_path.exists():
        try:
            with open(warm_report_path, encoding="utf-8") as f:
                warm = json.load(f)
        except Exception as e:
            log.debug(f"research_verdict: failed to read warm_start_report: {e}")
            warm = {}

    total_ws_candidates = 0
    if warm:
        total_ws_candidates = int(
            warm.get("total_candidates_replayed")
            or warm.get("total_candidates_evaluated")
            or warm.get("candidate_count_evaluated")
            or warm.get("candidates_replayed")
            or 0
        )
    warm_viable_seed_found = bool(warm.get("viable_seed_found") or warm.get("warm_start_used"))

    # Demo trades: closed trades since beginning of Demo (simple count)
    try:
        demo_trades = db.get_trades()
    except Exception as e:
        log.debug(f"research_verdict: get_trades failed: {e}")
        demo_trades = []
    total_demo_closed_trades = len([t for t in demo_trades if t.get("pnl") is not None])

    # Automation snapshot: evaluation/optimizer cycles and candidate ids
    snap = _load_automation_snapshot(db)
    completed_eval_cycles = 1 if snap.last_evaluation_run_id else 0
    completed_optimizer_cycles = 1 if snap.last_optimizer_run_id else 0
    best_candidate_config_id = snap.best_candidate_config_id
    candidate_exists = bool(best_candidate_config_id)

    # Candidate Demo metrics (if any)
    candidate_metrics: Dict[str, Any] = {}
    demo_profit_factor = 0.0
    demo_expectancy = 0.0
    demo_ultra_short_fraction = 0.0
    if candidate_exists:
        try:
            trades = db.get_trades(config_id=best_candidate_config_id)
            metrics = compute_core_metrics(trades)
            durations = get_trade_durations_sec(trades)
            policy = getattr(config, "research_policy", None)
            ultra_short_threshold = float(getattr(policy, "max_demo_ultra_short_trade_fraction_for_candidate_review", 0.25) and getattr(policy, "ultra_short_duration_sec", 60.0))  # type: ignore[attr-defined]
            # If policy has its own duration threshold, prefer that; else fall back to warm_start.ultra_short_duration_sec
            if getattr(policy, "ultra_short_duration_sec", None) is None:
                ultra_short_threshold = float(getattr(getattr(config, "warm_start", None) or {}, "ultra_short_duration_sec", 60.0))
            ultra_short_count = sum(1 for d in durations if d < ultra_short_threshold) if durations else 0
            demo_ultra_short_fraction = (ultra_short_count / len(durations)) if durations else 0.0
            candidate_metrics = {
                **metrics,
                "ultra_short_trade_fraction": demo_ultra_short_fraction,
            }
            demo_profit_factor = float(metrics.get("profit_factor") or 0.0)
            demo_expectancy = float(metrics.get("expectancy") or 0.0)
        except Exception as e:
            log.debug(f"research_verdict: candidate metrics failed: {e}")
            candidate_metrics = {}

    return {
        "total_warm_start_candidates": total_ws_candidates,
        "warm_start_viable_seed_found": warm_viable_seed_found,
        "total_demo_closed_trades": total_demo_closed_trades,
        "completed_eval_cycles": completed_eval_cycles,
        "completed_optimizer_cycles": completed_optimizer_cycles,
        "candidate_exists": candidate_exists,
        "best_candidate_config_id": best_candidate_config_id,
        "candidate_metrics": candidate_metrics,
        "demo_profit_factor": demo_profit_factor,
        "demo_expectancy": demo_expectancy,
        "demo_ultra_short_trade_fraction": demo_ultra_short_fraction,
        "automation_snapshot": asdict(snap),
        "warm_start_report": warm,
    }


def compute_research_verdict(config: Config, evidence: Dict[str, Any]) -> Tuple[str, list[str], Dict[str, Any]]:
    """Apply research_policy thresholds to evidence and return (verdict, reasons, summary)."""
    policy = getattr(config, "research_policy", None)
    if not policy or not getattr(policy, "enabled", True):
        return VERDICT_DISABLED, ["research_policy_disabled"], {"note": "research_policy.enabled=False"}

    reasons: list[str] = []

    min_ws_candidates = int(getattr(policy, "min_total_warm_start_candidates_before_strategy_judgment", 500))
    min_demo_trades = int(getattr(policy, "min_real_demo_closed_trades_before_strategy_judgment", 200))
    min_eval_cycles = int(getattr(policy, "min_completed_eval_cycles_before_strategy_judgment", 3))

    total_ws_candidates = int(evidence.get("total_warm_start_candidates") or 0)
    total_demo_trades = int(evidence.get("total_demo_closed_trades") or 0)
    completed_eval_cycles = int(evidence.get("completed_eval_cycles") or 0)

    # Candidate metrics thresholds
    min_pf = float(getattr(policy, "min_demo_profit_factor_for_candidate_review", 1.10))
    min_exp = float(getattr(policy, "min_demo_expectancy_for_candidate_review", 0.0))
    max_ultra = float(getattr(policy, "max_demo_ultra_short_trade_fraction_for_candidate_review", 0.25))
    require_no_surviving = bool(getattr(policy, "require_no_surviving_candidate_after_thresholds", True))

    demo_pf = float(evidence.get("demo_profit_factor") or 0.0)
    demo_exp = float(evidence.get("demo_expectancy") or 0.0)
    demo_ultra = float(evidence.get("demo_ultra_short_trade_fraction") or 0.0)
    candidate_exists = bool(evidence.get("candidate_exists"))

    candidate_ready = candidate_exists and demo_pf >= min_pf and demo_exp >= min_exp and demo_ultra <= max_ultra

    search_mature = total_ws_candidates >= min_ws_candidates
    demo_mature = total_demo_trades >= min_demo_trades and completed_eval_cycles >= min_eval_cycles

    if not search_mature:
        reasons.append("warm_start_candidates_below_threshold")
        verdict = VERDICT_SEARCH_NOT_MATURE
    elif not demo_mature:
        if total_demo_trades < min_demo_trades:
            reasons.append("demo_closed_trades_below_threshold")
        if completed_eval_cycles < min_eval_cycles:
            reasons.append("completed_eval_cycles_below_threshold")
        verdict = VERDICT_DEMO_VALIDATION_NOT_MATURE
    elif candidate_ready:
        reasons.append("candidate_ready_for_review")
        verdict = VERDICT_CANDIDATE_READY_FOR_REVIEW
    else:
        reasons.append("no_candidate_with_required_pf_and_expectancy")
        if require_no_surviving:
            verdict = VERDICT_STRATEGY_EDGE_DOUBTFUL
        else:
            verdict = VERDICT_DEMO_VALIDATION_NOT_MATURE

    summary = {
        "search_mature": search_mature,
        "demo_mature": demo_mature,
        "candidate_ready_for_review": candidate_ready,
        "total_warm_start_candidates": total_ws_candidates,
        "total_demo_closed_trades": total_demo_trades,
        "completed_eval_cycles": completed_eval_cycles,
        "min_total_warm_start_candidates_before_strategy_judgment": min_ws_candidates,
        "min_real_demo_closed_trades_before_strategy_judgment": min_demo_trades,
        "min_completed_eval_cycles_before_strategy_judgment": min_eval_cycles,
    }
    return verdict, reasons, summary


def write_research_verdict_artifact(config: Config, verdict_result: Dict[str, Any]) -> None:
    """Write research_verdict.json under artifacts/<instance>/research/."""
    artifacts_root = Path(config.artifacts_root)
    research_dir = artifacts_root / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "research_verdict.json"
    payload = {
        "timestamp_ms": int(time.time() * 1000),
        "operating_mode": getattr(config, "operating_mode", None),
        **verdict_result,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        log.warning(f"Write research_verdict.json failed: {e}")


def evaluate_research_verdict(config: Config) -> Dict[str, Any]:
    """
    Top-level helper: collect evidence, compute verdict, and (optionally) write artifact.

    Demo-only semantics; safe no-op when research_policy.enabled=False.
    """
    policy = getattr(config, "research_policy", None)
    if not policy or not getattr(policy, "enabled", True):
        verdict, reasons, summary = VERDICT_DISABLED, ["research_policy_disabled"], {"note": "research_policy.enabled=False"}
        return {"verdict": verdict, "reasons": reasons, "summary": summary, "evidence": {}}

    db = Database(config.database_path)
    try:
        evidence = collect_research_evidence(config, db)
    finally:
        db.close()

    verdict, reasons, summary = compute_research_verdict(config, evidence)
    result = {
        "verdict": verdict,
        "reasons": reasons,
        "summary": summary,
        "evidence": evidence,
    }
    if getattr(policy, "emit_verdict_artifact", True):
        write_research_verdict_artifact(config, result)
    return result

