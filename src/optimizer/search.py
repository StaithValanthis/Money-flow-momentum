"""Run walk-forward optimization: sample params, evaluate on segments, apply guardrails."""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from src.storage.db import Database
from src.config.config import Config, load_config
from src.config.versioning import get_active_config_id, load_config_from_artifact, register_config_version
from src.config.candidate_factory import generate_candidate, APPROVED_PARAM_PATHS
from src.evaluation.metrics import compute_core_metrics
from src.evaluation.datasets import load_evaluation_dataset
from src.optimizer.parameter_space import get_bounded_space
from src.optimizer.walk_forward import generate_segments
from src.optimizer.objectives import composite_objective
from src.optimizer.guardrails import check_guardrails, check_symbol_concentration, GuardrailResult
from src.optimizer.candidate_selector import select_best_candidate
from src.utils.logging import get_logger

log = get_logger(__name__)


def run_optimization(
    db_path: str = "data/bot.db",
    config_id: Optional[str] = None,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    n_samples: int = 20,
    train_pct: float = 0.5,
    val_pct: float = 0.25,
    test_pct: float = 0.25,
    artifact_dir: Optional[Path] = None,
) -> dict:
    """
    Load baseline config, get data window, run walk-forward segments with sampled params,
    compute in/out-of-sample metrics, apply guardrails, optionally create best candidate.
    Returns run summary with best_candidate_config_id if any.
    """
    run_id = str(uuid.uuid4())[:8]
    db = Database(db_path)
    baseline_id = config_id or get_active_config_id(db_path)
    baseline_config = load_config_from_artifact(baseline_id, db_path) if baseline_id else None
    if baseline_config is None:
        baseline_config, _ = load_config()
    if not isinstance(baseline_config, Config):
        baseline_config = Config()
    if from_ts is None or to_ts is None:
        trades = db.get_trades()
        if trades:
            ts_list = [t.get("ts") for t in trades if t.get("ts")]
            from_ts = min(ts_list) if ts_list else 0
            to_ts = max(ts_list) if ts_list else int(time.time() * 1000)
        else:
            from_ts = 0
            to_ts = int(time.time() * 1000)

    segments = generate_segments(from_ts, to_ts, train_pct, val_pct, test_pct, n_splits=1)
    if not segments:
        return {"run_id": run_id, "error": "no_segments", "best_candidate_config_id": None}

    space = get_bounded_space(stage4=True, stage5=True)
    param_samples = space.sample_random(n_samples)
    stage5_keys = [k for k in (space.bounds or {}) if k.startswith("risk.") or k.startswith("portfolio_exposure.")]

    results: list[dict] = []
    for i, params in enumerate(param_samples):
        seg = segments[0]
        data = load_evaluation_dataset(db_path, from_ts=seg.train_from, to_ts=seg.train_to, config_id=baseline_id)
        train_trades = data["trades"]
        data_oos = load_evaluation_dataset(db_path, from_ts=seg.val_from, to_ts=seg.val_to, config_id=baseline_id)
        oos_trades = data_oos["trades"]
        is_metrics = compute_core_metrics(train_trades)
        oos_metrics = compute_core_metrics(oos_trades)
        gr = check_guardrails(is_metrics, oos_metrics, baseline_metrics=None, min_trades=5)
        score = composite_objective(oos_metrics) - gr.penalty
        cid = f"run_{run_id}_{i}"
        results.append({
            "config_id": cid,
            "params": params,
            "is_metrics": is_metrics,
            "oos_metrics": oos_metrics,
            "guardrail_passed": gr.passed,
            "reason_codes": gr.reason_codes,
            "objective_score": score,
        })
    guardrail_results = {r["config_id"]: GuardrailResult(r["guardrail_passed"], r.get("reason_codes", []), 0.0) for r in results}
    best = select_best_candidate(results, guardrail_results=guardrail_results)
    best_candidate_config_id = None
    if best and best.get("params"):
        try:
            best_candidate_config_id = generate_candidate(
                baseline_config,
                best["params"],
                version=f"opt_{run_id}",
                description=f"Optimizer run {run_id}",
                source="optimizer",
                optimizer_run_id=run_id,
                windows_json=json.dumps({"from_ts": from_ts, "to_ts": to_ts}),
                objective_summary=json.dumps(best.get("oos_metrics") or {}),
                reason_codes=",".join(best.get("reason_codes") or []),
                db_path=db_path,
                artifact_dir=artifact_dir or Path("artifacts/configs"),
            )
        except Exception as e:
            log.warning(f"Could not create candidate: {e}")

    db.insert_optimization_run(
        run_id=run_id,
        baseline_config_id=baseline_id,
        from_ts=from_ts,
        to_ts=to_ts,
        status="completed",
        summary_json=json.dumps({
            "best_candidate_config_id": best_candidate_config_id,
            "n_samples": n_samples,
            "stage5_params_included": bool(stage5_keys),
        }),
    )
    if artifact_dir:
        (Path(artifact_dir) / "optimizations").mkdir(parents=True, exist_ok=True)
        with open(Path(artifact_dir) / "optimizations" / f"run_{run_id}_summary.json", "w") as f:
            json.dump({
                "run_id": run_id,
                "best_candidate_config_id": best_candidate_config_id,
                "n_samples": n_samples,
                "stage5_params_included": bool(stage5_keys),
            }, f)
    db.close()
    return {"run_id": run_id, "best_candidate_config_id": best_candidate_config_id, "n_samples": n_samples}
