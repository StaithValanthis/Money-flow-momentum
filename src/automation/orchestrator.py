"""Demo orchestration: readiness -> evaluation -> optimization -> shadow -> recommendation.

This module is intentionally conservative and Demo-only. It never:
* promotes configs
* switches environments
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Tuple

from src.journal.logger import append_journal_event as journal_append
from src.lifecycle.logger import append_demo_lifecycle_event
from src.automation.state import (
    AutomationSnapshot,
    RECOMMENDATION_CONTINUE_DEMO,
    RECOMMENDATION_DEMO_AUTO_ADOPTED,
    RECOMMENDATION_NOT_READY,
    RECOMMENDATION_READY_FOR_CONFIG_REVIEW,
    STATE_AWAITING_MANUAL_APPROVAL,
    STATE_BLOCKED_BY_BURNIN,
    STATE_BLOCKED_BY_HEALTH,
    STATE_BLOCKED_BY_KILL_SWITCH,
    STATE_CANDIDATE_AVAILABLE,
    STATE_CONTINUE_DEMO_NO_CANDIDATE,
    STATE_DEMO_AUTO_ADOPTED,
    STATE_EVALUATING,
    STATE_IDLE,
    STATE_OPTIMIZING,
    STATE_READY_FOR_EVALUATION,
    STATE_READY_FOR_OPTIMIZATION,
    STATE_SHADOW_RUNNING,
    STATE_WAITING_FOR_BURNIN_DATA,
    transition,
)
from src.config.config import Config, EnvSettings, get_bybit_env, load_config
from src.config.versioning import activate_config_version, get_active_config_id
from src.evaluation.evaluator import Evaluator
from src.optimizer.search import run_optimization
from src.shadow.shadow_runner import ShadowRunner
from src.storage.artifacts import automation_dir, pipeline_dir
from src.storage.db import Database
from src.utils.logging import get_logger
from src.research.verdict import evaluate_research_verdict
from src.validation.readiness import READINESS_NOT_READY, compute_readiness

log = get_logger(__name__)


def _load_config_and_db(config_path: Optional[Path]) -> Tuple[Config, EnvSettings, Database]:
    config, env = load_config(config_path)
    db = Database(config.database_path)
    return config, env, db


def _is_demo_env(config: Config, env: EnvSettings) -> bool:
    """True when we are in Demo + demo-phase burn-in."""
    env_t = get_bybit_env(env)
    burn = getattr(config, "burn_in", None)
    phase = getattr(burn, "burn_in_phase", "demo") if burn else "demo"
    return env_t == "demo" and bool(burn and getattr(burn, "burn_in_enabled", False) and phase == "demo")


def _get_demo_probation_summary(config: Config) -> Optional[dict]:
    """Return a short demo probation summary for automation details, or None."""
    if not getattr(getattr(config, "demo_probation", None), "enabled", False):
        return None
    try:
        from src.demo_probation import get_current_probation_status, get_probation_record
        from src.config.versioning import get_active_config_id
        prob = get_current_probation_status(config.database_path)
        if prob:
            return {"status": "IN_PROGRESS", "candidate_config_id": prob.get("config_id")}
        aid = get_active_config_id(config.database_path)
        rec = get_probation_record(aid, config.database_path) if aid else None
        if rec:
            s = rec.get("lifecycle_state", "")
            if s == "DEMO_PROBATION_PASSED":
                return {"status": "PASSED", "candidate_config_id": aid}
            if s == "DEMO_PROBATION_FAILED":
                return {"status": "FAILED", "candidate_config_id": aid}
    except Exception:
        pass
    return None


def _load_snapshot(db: Database) -> AutomationSnapshot:
    row = db.get_automation_state()
    return AutomationSnapshot.from_db(row or None)


def _persist_snapshot(db: Database, snap: AutomationSnapshot) -> None:
    db.upsert_automation_state(snap.to_db_dict())


def _write_recommendation_artifacts(config: Config, snap: AutomationSnapshot, details: dict[str, Any]) -> None:
    base = automation_dir(Path(config.artifacts_root))
    base.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    payload: dict[str, Any] = {
        "timestamp_ms": ts,
        "state": snap.state,
        "operating_mode": getattr(config, "operating_mode", None) or "live_guarded",
        "automation": {
            "enabled": getattr(config, "automation", None).enabled if getattr(config, "automation", None) else False,
        },
        "snapshot": asdict(snap),
    }
    payload.update(details)

    # Optional research verdict (Demo-only; advisory)
    policy = getattr(config, "research_policy", None)
    if policy and getattr(policy, "enabled", True) and getattr(policy, "emit_verdict_in_status", True):
        try:
            verdict_result = evaluate_research_verdict(config)
            payload["research_verdict"] = verdict_result.get("verdict")
            payload["research_verdict_reasons"] = verdict_result.get("reasons")
        except Exception as e:
            log.debug(f"automation: research_verdict computation failed: {e}")

    json_path = base / f"automation_status_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    # Stable symlink/alias for latest
    latest_json = base / "automation_status.json"
    try:
        if latest_json.exists():
            latest_json.unlink()
        latest_json.hardlink_to(json_path)
    except Exception:
        # Fallback: copy-on-write
        with open(latest_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

    # Markdown summary
    md_path = base / "automation_status.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Demo Automation Status\n\n")
        f.write(f"- state: {snap.state}\n")
        f.write(f"- last_recommendation_status: {snap.last_recommendation_status or 'N/A'}\n")
        f.write(f"- active_config_id: {details.get('active_config_id')}\n")
        f.write(f"- last_readiness_classification: {snap.last_readiness_classification or 'N/A'}\n")
        f.write(f"- last_evaluation_run_id: {snap.last_evaluation_run_id or 'N/A'}\n")
        f.write(f"- last_optimizer_run_id: {snap.last_optimizer_run_id or 'N/A'}\n")
        f.write(f"- best_candidate_config_id: {snap.best_candidate_config_id or 'none'}\n")
        f.write(f"- shadow_candidate_config_id: {snap.shadow_candidate_config_id or 'none'}\n")
        f.write(f"- last_demo_adoption_ts: {snap.last_demo_adoption_ts or 'N/A'}\n")
        if snap.blocked_reason:
            f.write(f"- blocked_reason: {snap.blocked_reason}\n")
        if details.get("blocked_hint"):
            f.write(f"- blocked_hint: {details['blocked_hint']}\n")
        if details.get("recommendation_message"):
            f.write(f"\n## Recommendation\n\n{details['recommendation_message']}\n\n")
        if snap.last_error:
            f.write(f"- last_error: {snap.last_error}\n")
        f.write("\n## Next manual commands\n\n")
        for cmd in details.get("next_commands", []):
            f.write(f"- {cmd}\n")


def run_demo_automation_cycle(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Single orchestration cycle. Safe to call from CLI or scheduler."""
    config, env, db = _load_config_and_db(config_path)
    try:
        auto = getattr(config, "automation", None)
        if not auto or not auto.enabled or not auto.demo_orchestration_enabled:
            log.debug("Automation disabled in config; staying idle.")
            snap = _load_snapshot(db)
            snap = transition(snap, STATE_IDLE)
            _persist_snapshot(db, snap)
            details = {
                "reason": "automation_disabled",
                "active_config_id": get_active_config_id(config.database_path),
                "next_commands": [],
            }
            _write_recommendation_artifacts(config, snap, details)
            return {"snapshot": asdict(snap), "details": details}

        if not _is_demo_env(config, env):
            log.debug("Automation only runs in Demo burn-in; environment does not qualify.")
            snap = _load_snapshot(db)
            snap = transition(snap, STATE_IDLE, reason="not_demo_burnin")
            _persist_snapshot(db, snap)
            details = {
                "reason": "not_demo_burnin",
                "active_config_id": get_active_config_id(config.database_path),
                "next_commands": [],
            }
            _write_recommendation_artifacts(config, snap, details)
            return {"snapshot": asdict(snap), "details": details}

        snap = _load_snapshot(db)
        now_ms = int(time.time() * 1000)

        # --- 1) Readiness / burn-in status ---
        burn = getattr(config, "burn_in", None)
        window_hours = getattr(burn, "burn_in_required_report_window_hours", 24.0) if burn else 24.0
        from_ts = now_ms - int(window_hours * 3600 * 1000)
        art_root = Path(config.artifacts_root)
        hb_path = art_root / "heartbeat.json"
        config_id = get_active_config_id(config.database_path)
        try:
            readiness = compute_readiness(
                db,
                heartbeat_path=hb_path,
                config_id=config_id,
                window_hours=window_hours,
                burn_in_phase=getattr(burn, "burn_in_phase", "demo") if burn else "demo",
            )
        except Exception as e:
            log.error(f"Automation readiness error: {e}")
            snap.last_error = str(e)
            snap = transition(snap, STATE_ERROR)
            _persist_snapshot(db, snap)
            details = {
                "reason": "readiness_error",
                "active_config_id": config_id,
                "next_commands": ["python run_bot.py burnin readiness --window %.1f --output %s" % (window_hours, art_root / "burnin")],
            }
            _write_recommendation_artifacts(config, snap, details)
            return {"snapshot": asdict(snap), "details": details}

        snap.last_readiness_ts = now_ms
        snap.last_readiness_classification = readiness.classification
        trade_count = int(readiness.details.get("trade_count", 0) or 0)
        kill_switch_count = int(readiness.details.get("kill_switch_count", 0) or 0)
        gate_breaches = int(readiness.details.get("burnin_gate_breach_count", 0) or 0)

        # Blocked conditions: always set blocked state (do not collapse to WAITING_FOR_BURNIN_DATA)
        if kill_switch_count > 0:
            snap.last_recommendation_status = RECOMMENDATION_NOT_READY
            snap = transition(snap, STATE_BLOCKED_BY_KILL_SWITCH, reason="kill_switch_in_window")
            _persist_snapshot(db, snap)
            details = {
                "reason": "kill_switch_in_window",
                "active_config_id": config_id,
                "readiness_details": readiness.details,
                "next_commands": [
                    "python run_bot.py burnin report --window %.1f" % window_hours,
                    "python run_bot.py burnin readiness --window %.1f --output %s" % (window_hours, art_root / "burnin"),
                ],
            }
            _write_recommendation_artifacts(config, snap, details)
            journal_append(
                config.artifacts_root, "RUNTIME", "blocked",
                instance=getattr(config, "instance_name", None) or "demo",
                reason="kill_switch_in_window",
                status=STATE_BLOCKED_BY_KILL_SWITCH,
            )
            return {"snapshot": asdict(snap), "details": details}

        # --- Demo probation: evaluate active candidate from real Demo data ---
        prob = getattr(config, "demo_probation", None)
        if prob and getattr(prob, "enabled", False):
            from src.demo_probation import evaluate_probation, apply_probation_result, get_current_probation_status
            from src.demo_probation.artifacts import build_probation_status_payload, write_probation_status_artifact
            prob_status = get_current_probation_status(config.database_path)
            if prob_status:
                p_status, p_lifecycle, p_reasons, p_metrics, p_failure_type = evaluate_probation(
                    config.database_path, config, config_id=prob_status.get("config_id")
                )
                if p_status == "PASSED":
                    apply_probation_result(
                        prob_status["config_id"], config.database_path, config,
                        p_status, p_lifecycle, p_reasons, p_metrics, failure_reason_type=p_failure_type,
                    )
                    instance = getattr(config, "instance_name", None) or "demo"
                    payload = build_probation_status_payload(
                        prob_status["config_id"], p_lifecycle, p_status, p_metrics, p_reasons,
                        prob_status.get("started_at_ts"), prob_status.get("updated_at_ts"),
                        int(time.time() * 1000), int(time.time() * 1000), True,
                    )
                    write_probation_status_artifact(config.artifacts_root, instance, payload)
                    log.info("Demo probation passed; candidate promoted to active Demo baseline")
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "PROBATION", "passed", config_id=prob_status["config_id"],
                    )
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "DEMO_BASELINE", "promoted", config_id=prob_status["config_id"],
                    )
                elif p_status == "FAILED":
                    apply_probation_result(
                        prob_status["config_id"], config.database_path, config,
                        p_status, p_lifecycle, p_reasons, p_metrics, failure_reason_type=p_failure_type,
                    )
                    instance = getattr(config, "instance_name", None) or "demo"
                    payload = build_probation_status_payload(
                        prob_status["config_id"], p_lifecycle, p_status, p_metrics, p_reasons,
                        prob_status.get("started_at_ts"), prob_status.get("updated_at_ts"),
                        int(time.time() * 1000), None, False, failure_reason_type=p_failure_type,
                    )
                    write_probation_status_artifact(config.artifacts_root, instance, payload)
                    log.warning("Demo probation failed: %s (reason_type=%s)", p_reasons, p_failure_type or "timer_evaluated")
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "PROBATION", "failed",
                        config_id=prob_status["config_id"],
                        reason="; ".join(p_reasons) if p_reasons else None,
                        failure_reason_type=p_failure_type,
                        metrics=p_metrics,
                    )

        if gate_breaches > 0:
            snap.last_recommendation_status = RECOMMENDATION_NOT_READY
            snap = transition(snap, STATE_BLOCKED_BY_BURNIN, reason="burnin_gate_breach")
            _persist_snapshot(db, snap)
            breach_hint = "Review %s and fix limits or wait for window to clear." % (art_root / "burnin")
            if gate_breaches > 100:
                breach_hint = "Many gate breaches ({}). Run: python run_bot.py burnin report --window %.1f ; review config limits and breach reasons.".format(gate_breaches) % window_hours
            details = {
                "reason": "burnin_gate_breach",
                "active_config_id": config_id,
                "readiness_details": readiness.details,
                "blocked_hint": breach_hint,
                "next_commands": [
                    "python run_bot.py burnin report --window %.1f" % window_hours,
                    "python run_bot.py burnin readiness --window %.1f --output %s" % (window_hours, art_root / "burnin"),
                ],
            }
            _write_recommendation_artifacts(config, snap, details)
            journal_append(
                config.artifacts_root, "RUNTIME", "blocked",
                instance=getattr(config, "instance_name", None) or "demo",
                reason="burnin_gate_breach",
                status=STATE_BLOCKED_BY_BURNIN,
            )
            return {"snapshot": asdict(snap), "details": details}

        # trade_count > 0 but readiness NOT_READY (e.g. kill switch already handled above) => blocked by health
        if trade_count > 0 and readiness.classification == READINESS_NOT_READY:
            snap.last_recommendation_status = RECOMMENDATION_NOT_READY
            reason = readiness.message or readiness.classification
            snap = transition(snap, STATE_BLOCKED_BY_HEALTH, reason=reason)
            _persist_snapshot(db, snap)
            details = {
                "reason": "readiness_not_ok",
                "active_config_id": config_id,
                "readiness_details": readiness.details,
                "readiness_classification": readiness.classification,
                "readiness_message": readiness.message,
                "next_commands": [
                    "python run_bot.py burnin report --window %.1f" % window_hours,
                    "python run_bot.py burnin readiness --window %.1f --output %s" % (window_hours, art_root / "burnin"),
                ],
            }
            _write_recommendation_artifacts(config, snap, details)
            journal_append(
                config.artifacts_root, "RUNTIME", "blocked",
                instance=getattr(config, "instance_name", None) or "demo",
                reason=reason,
                status=STATE_BLOCKED_BY_HEALTH,
            )
            return {"snapshot": asdict(snap), "details": details}

        if trade_count <= 0:
            snap.last_recommendation_status = RECOMMENDATION_CONTINUE_DEMO
            snap = transition(snap, STATE_WAITING_FOR_BURNIN_DATA, reason="no_trades")
            _persist_snapshot(db, snap)
            details = {
                "reason": "no_trades_in_window",
                "active_config_id": config_id,
                "readiness_details": readiness.details,
                "next_commands": [],
            }
            _write_recommendation_artifacts(config, snap, details)
            return {"snapshot": asdict(snap), "details": details}

        # At this point we have some data and are not blocked.
        snap = transition(snap, STATE_READY_FOR_EVALUATION)

        # --- 2) Evaluation (conservative cadence) ---
        min_trades_eval = getattr(auto, "min_trades_for_auto_evaluation", 50)
        min_eval_interval_ms = int(getattr(auto, "min_hours_between_evaluations", 6.0) * 3600 * 1000)
        should_eval = (
            trade_count >= min_trades_eval
            and (snap.last_evaluation_ts is None or now_ms - snap.last_evaluation_ts >= min_eval_interval_ms)
        )

        last_eval_summary: dict[str, Any] | None = None
        if should_eval:
            snap = transition(snap, STATE_EVALUATING)
            try:
                ev = Evaluator(config.database_path)
                eval_result = ev.run(from_ts=from_ts, to_ts=now_ms, config_id=config_id, symbol=None)
                snap.last_evaluation_run_id = eval_result.get("run_id")
                snap.last_evaluation_ts = now_ms
                last_eval_summary = eval_result
                log.info(f"Automation evaluation complete: run_id={snap.last_evaluation_run_id}")
            except Exception as e:
                log.error(f"Automation evaluation error: {e}")
                snap.last_error = str(e)
                snap = transition(snap, STATE_ERROR)
                _persist_snapshot(db, snap)
                details = {
                    "reason": "evaluation_error",
                    "active_config_id": config_id,
                    "next_commands": [
                        "python run_bot.py evaluate --from-date YYYY-MM-DD --to-date YYYY-MM-DD",
                    ],
                }
                _write_recommendation_artifacts(config, snap, details)
                return {"snapshot": asdict(snap), "details": details}

        # --- 3) Optimizer (if allowed) ---
        min_opt_interval_ms = int(getattr(auto, "min_hours_between_optimizer_runs", 24.0) * 3600 * 1000)
        require_readiness = getattr(auto, "require_readiness_for_optimizer", True)
        readiness_ok = readiness.classification != READINESS_NOT_READY
        should_opt = (
            trade_count >= min_trades_eval
            and (not require_readiness or readiness_ok)
            and (snap.last_optimizer_ts is None or now_ms - snap.last_optimizer_ts >= min_opt_interval_ms)
        )

        best_candidate_id: Optional[str] = None
        optimizer_run_id: Optional[str] = None

        if should_opt:
            snap = transition(snap, STATE_READY_FOR_OPTIMIZATION)
            snap = transition(snap, STATE_OPTIMIZING)
            try:
                opt_out = run_optimization(
                    db_path=config.database_path,
                    config_id=config_id,
                    from_ts=from_ts,
                    to_ts=now_ms,
                    n_samples=getattr(getattr(config, "automation", None), "min_trades_for_auto_evaluation", 20),
                )
                optimizer_run_id = opt_out.get("run_id")
                best_candidate_id = opt_out.get("best_candidate_config_id")
                snap.last_optimizer_run_id = optimizer_run_id
                snap.last_optimizer_ts = now_ms
                snap.best_candidate_config_id = best_candidate_id
                if best_candidate_id:
                    snap = transition(snap, STATE_CANDIDATE_AVAILABLE)
                log.info(
                    f"Automation optimizer complete: run_id={optimizer_run_id} best_candidate={best_candidate_id}"
                )
            except Exception as e:
                log.error(f"Automation optimizer error: {e}")
                snap.last_error = str(e)
                snap = transition(snap, STATE_ERROR)
                _persist_snapshot(db, snap)
                details = {
                    "reason": "optimizer_error",
                    "active_config_id": config_id,
                    "next_commands": [
                        "python run_bot.py optimize run --from-date YYYY-MM-DD --to-date YYYY-MM-DD",
                    ],
                }
                _write_recommendation_artifacts(config, snap, details)
                return {"snapshot": asdict(snap), "details": details}

        # --- 4) Auto shadow for best candidate ---
        auto_shadow = getattr(auto, "auto_start_shadow_for_best_candidate", True)
        if auto_shadow and snap.best_candidate_config_id:
            cid = snap.best_candidate_config_id
            # Avoid restarting for the same candidate repeatedly
            if cid != snap.shadow_candidate_config_id:
                runner = ShadowRunner(config.database_path)
                started = runner.start(cid)
                if started:
                    snap.shadow_candidate_config_id = cid
                    snap = transition(snap, STATE_SHADOW_RUNNING)
                    log.info(f"Automation: shadow started for candidate {cid}")

        # --- 4.5) Demo-only auto-adopt: activate best candidate as new Demo active config ---
        auto_adopt = getattr(auto, "auto_adopt_demo_candidates", False)
        min_trades_adopt = getattr(auto, "min_trades_for_demo_adoption", 50)
        min_hours_adopt = getattr(auto, "min_hours_between_demo_adoptions", 24.0)
        require_shadow_adopt = getattr(auto, "require_shadow_before_demo_adoption", False)
        cooldown_ok = (
            snap.last_demo_adoption_ts is None
            or (now_ms - snap.last_demo_adoption_ts) >= int(min_hours_adopt * 3600 * 1000)
        )
        shadow_ok = not require_shadow_adopt or (snap.shadow_candidate_config_id == snap.best_candidate_config_id)
        if (
            auto_adopt
            and snap.best_candidate_config_id
            and trade_count >= min_trades_adopt
            and cooldown_ok
            and shadow_ok
        ):
            if activate_config_version(
                snap.best_candidate_config_id,
                config.database_path,
                reason="demo_auto_adopt",
                manual=False,
            ):
                snap.last_demo_adoption_ts = now_ms
                snap.last_recommendation_status = RECOMMENDATION_DEMO_AUTO_ADOPTED
                snap = transition(snap, STATE_DEMO_AUTO_ADOPTED)
                _persist_snapshot(db, snap)
                new_active = get_active_config_id(config.database_path)
                details = {
                    "reason": "demo_auto_adopted",
                    "active_config_id": new_active,
                    "previous_active_config_id": config_id,
                    "adopted_config_id": snap.best_candidate_config_id,
                    "recommendation_message": (
                        "Candidate met Demo auto-adopt rules and has been activated for Demo research. "
                        "Restart Demo to use new config. Live unchanged."
                    ),
                    "next_commands": [
                        "Restart Demo process to load new active config.",
                        "python run_bot.py promote --config-id <id>  # when ready to promote to Live",
                    ],
                }
                _write_recommendation_artifacts(config, snap, details)
                return {"snapshot": asdict(snap), "details": details}
            # activation failed (e.g. config not in DB); fall through to normal recommendation

        # --- 5) Recommendation summary ---
        # For Demo we conservatively say:
        # - if no candidate: continue demo
        # - if candidate exists: ready for config review (manual promotion only)
        if snap.best_candidate_config_id:
            snap.last_recommendation_status = RECOMMENDATION_READY_FOR_CONFIG_REVIEW
            snap = transition(snap, STATE_AWAITING_MANUAL_APPROVAL)
            journal_append(
                config.artifacts_root, "CANDIDATE", "ready_for_review",
                instance=getattr(config, "instance_name", None) or "demo",
                candidate_config_id=snap.best_candidate_config_id,
            )
        else:
            snap.last_recommendation_status = RECOMMENDATION_CONTINUE_DEMO
            if snap.state not in (STATE_SHADOW_RUNNING, STATE_CANDIDATE_AVAILABLE):
                if trade_count <= 0:
                    snap = transition(snap, STATE_WAITING_FOR_BURNIN_DATA)
                elif snap.last_evaluation_run_id and snap.last_optimizer_run_id:
                    snap = transition(snap, STATE_CONTINUE_DEMO_NO_CANDIDATE)
                else:
                    snap = transition(snap, STATE_READY_FOR_EVALUATION)

        _persist_snapshot(db, snap)

        active_config_id = config_id
        next_commands: list[str] = []
        recommendation_message: Optional[str] = None
        if snap.best_candidate_config_id:
            # Operator can inspect candidate, shadow, and then decide on promotion.
            best_cid = snap.best_candidate_config_id
            next_commands.extend(
                [
                    f"python run_bot.py candidates list",
                    f"python run_bot.py shadow report --candidate-config-id {best_cid}",
                    f"python run_bot.py promote --config-id {best_cid}",
                    "python run_bot.py promote-env",  # env promotion remains fully manual
                ]
            )
        else:
            next_commands.append("python run_bot.py burnin report --window %.1f" % window_hours)
            if snap.state == STATE_CONTINUE_DEMO_NO_CANDIDATE:
                recommendation_message = (
                    "Evaluation and optimizer completed, but no candidate met thresholds yet. Continue Demo data collection."
                )

        details = {
            "active_config_id": active_config_id,
            "readiness_details": readiness.details,
            "last_evaluation_summary": last_eval_summary,
            "optimizer_run_id": snap.last_optimizer_run_id,
            "candidate_count": None,
            "next_commands": next_commands,
        }
        if recommendation_message is not None:
            details["recommendation_message"] = recommendation_message
        prob_summary = _get_demo_probation_summary(config)
        if prob_summary:
            details["demo_probation"] = prob_summary
        _write_recommendation_artifacts(config, snap, details)
        return {"snapshot": asdict(snap), "details": details}
    finally:
        db.close()


def get_automation_status(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Lightweight helper to read and print automation snapshot."""
    config, env, db = _load_config_and_db(config_path)
    try:
        snap = _load_snapshot(db)
        base = automation_dir(Path(config.artifacts_root))
        latest_json = base / "automation_status.json"
        artifact_info: dict[str, Any] = {}
        if latest_json.exists():
            try:
                with open(latest_json, "r", encoding="utf-8") as f:
                    artifact_info = json.load(f)
            except Exception as e:
                log.debug(f"Read automation_status.json failed: {e}")
        return {
            "automation_enabled": getattr(config, "automation", None).enabled if getattr(config, "automation", None) else False,
            "env": get_bybit_env(env),
            "snapshot": asdict(snap),
            "artifact": artifact_info,
        }
    finally:
        db.close()

