"""
Parameter-aware warm-start candidate search.

Evaluates multiple candidate configs by running strategy replay on candles for each;
ranks by replay metrics and guardrails; returns the best candidate for Demo seeding.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.config.config import Config
from src.config.candidate_factory import build_config_from_params
from src.optimizer.parameter_space import get_bounded_space
from src.optimizer.objectives import composite_objective
from src.optimizer.guardrails import check_guardrails, GuardrailResult
from src.optimizer.candidate_selector import select_best_candidate
from src.evaluation.metrics import compute_core_metrics
from src.evaluation.datasets import compute_realized_pnl_by_pairing
from src.utils.logging import get_logger

from src.warm_start.strategy_replay import replay_strategy_from_candles

log = get_logger(__name__)


def run_warm_start_candidate_search(
    baseline_config: Config,
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
    n_samples: int = 15,
    min_trades_guardrail: int = 5,
    require_profitable: bool = True,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Sample candidate parameter sets, replay strategy on candles for each,
    compute metrics and guardrails, select best. Returns (best_result, all_results).

    best_result is None if no candidate passes guardrails or is profitable (when required).
    """
    if not candles_by_symbol:
        return None, []

    space = get_bounded_space(stage4=True, stage5=True)
    param_samples = space.sample_random(n_samples)
    results: List[Dict[str, Any]] = []

    for i, params in enumerate(param_samples):
        candidate_config = build_config_from_params(baseline_config, params)
        if not candidate_config:
            continue
        try:
            trades, _ = replay_strategy_from_candles(candidate_config, candles_by_symbol)
        except Exception as e:
            log.debug(f"Warm-start replay candidate {i}: {e}")
            continue
        paired = compute_realized_pnl_by_pairing(trades)
        metrics = compute_core_metrics(paired)
        gr = check_guardrails(
            metrics,
            metrics,
            baseline_metrics=None,
            min_trades=min_trades_guardrail,
        )
        score = composite_objective(metrics) - gr.penalty
        cid = f"ws_{i}"
        results.append({
            "config_id": cid,
            "params": params,
            "oos_metrics": metrics,
            "guardrail_passed": gr.passed,
            "reason_codes": gr.reason_codes,
            "objective_score": score,
        })
    guardrail_results = {
        r["config_id"]: GuardrailResult(
            r["guardrail_passed"],
            r.get("reason_codes", []),
            0.0,
        )
        for r in results
    }
    best = select_best_candidate(results, guardrail_results=guardrail_results)
    if best and require_profitable:
        total_pnl = float((best.get("oos_metrics") or {}).get("total_pnl") or 0)
        if total_pnl <= 0:
            best = None
    return best, results
