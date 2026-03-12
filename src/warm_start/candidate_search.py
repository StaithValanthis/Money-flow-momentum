"""
Parameter-aware warm-start candidate search.

Evaluates multiple candidate configs by running strategy replay on candles for each;
ranks by replay metrics and guardrails; returns the best candidate for Demo seeding.
Supports a hard runtime budget; on timeout returns best acceptable candidate seen so far.
"""

from __future__ import annotations

import time
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
    max_runtime_seconds: Optional[int] = None,
    start_time: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Sample candidate parameter sets, replay strategy on candles for each,
    compute metrics and guardrails, select best. Returns (best_result, all_results, meta).

    If max_runtime_seconds and start_time are set, stop when elapsed >= max_runtime_seconds
    and return best acceptable candidate from results so far (or None). meta includes timeout_hit, elapsed_seconds.
    """
    t0 = start_time if start_time is not None else time.time()
    if not candles_by_symbol:
        return None, [], {
            "candidates_invalid": 0,
            "candidates_replayed": 0,
            "no_trades_reason": "no_candles",
            "timeout_hit": False,
            "elapsed_seconds": time.time() - t0,
            "candidate_count_requested": n_samples,
        }

    space = get_bounded_space(stage4=True, stage5=True)
    param_samples = space.sample_random(n_samples)
    results: List[Dict[str, Any]] = []
    candidates_invalid = 0
    replay_trade_counts: List[int] = []
    timeout_hit = False
    best_score_so_far: Optional[float] = None

    for i, params in enumerate(param_samples):
        if max_runtime_seconds is not None and start_time is not None:
            elapsed = time.time() - start_time
            if elapsed >= max_runtime_seconds:
                log.warning("Warm-start runtime budget reached (%.0fs); stopping after %d candidates", max_runtime_seconds, i)
                timeout_hit = True
                break

        candidate_config = build_config_from_params(baseline_config, params)
        if not candidate_config:
            candidates_invalid += 1
            continue
        try:
            trades, replay_meta = replay_strategy_from_candles(candidate_config, candles_by_symbol)
        except Exception as e:
            log.debug("Warm-start replay candidate %s: %s", i, e)
            candidates_invalid += 1
            continue
        trade_count = replay_meta.get("trade_count", 0) or len([t for t in trades if t.get("pnl") is not None])
        replay_trade_counts.append(trade_count)
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
        if gr.passed and (best_score_so_far is None or score > best_score_so_far):
            best_score_so_far = score
        log.info(
            "Warm-start candidate %d/%d replayed trades=%s score=%.4f best_so_far=%s",
            i + 1,
            len(param_samples),
            trade_count,
            score,
            best_score_so_far if best_score_so_far is not None else "—",
        )

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

    no_trades_reason = None
    if replay_trade_counts and max(replay_trade_counts) == 0:
        no_trades_reason = "all_replay_runs_produced_zero_trades"
    elif not results and candidates_invalid == len(param_samples):
        no_trades_reason = "all_candidates_invalid_or_replay_failed"

    elapsed_seconds = time.time() - t0
    meta = {
        "candidates_invalid": candidates_invalid,
        "candidates_replayed": len(results),
        "no_trades_reason": no_trades_reason,
        "timeout_hit": timeout_hit,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "candidate_count_requested": n_samples,
    }
    return best, results, meta
