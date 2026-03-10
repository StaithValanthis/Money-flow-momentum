"""Select best candidate from optimization results."""

from typing import Any, Optional

from src.optimizer.objectives import composite_objective
from src.optimizer.guardrails import GuardrailResult


def select_best_candidate(
    results: list[dict],
    guardrail_results: Optional[dict[str, GuardrailResult]] = None,
    objective_key: str = "objective_score",
) -> Optional[dict]:
    """
    From list of {config_id, params, is_metrics, oos_metrics, ...}, pick best by composite objective.
    If guardrail_results[config_id].passed is False, exclude that candidate.
    """
    guardrail_results = guardrail_results or {}
    candidates = []
    for r in results:
        cid = r.get("config_id")
        if cid and not guardrail_results.get(cid, GuardrailResult(True, [], 0.0)).passed:
            continue
        oos = r.get("oos_metrics") or r.get("out_of_sample_metrics") or {}
        score = r.get(objective_key)
        if score is None:
            score = composite_objective(oos)
        candidates.append((score, r))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]
