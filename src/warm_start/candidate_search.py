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
from src.utils.logging import get_logger

from src.warm_start.backtest_engine import run_backtest_on_candles

log = get_logger(__name__)


_PROTECTION_PARAM_TO_REPORT_KEY: dict[str, str] = {
    "stop_tp.atr_multiplier_sl": "atr_multiplier_sl",
    "stop_tp.tp1_r_multiple": "tp1_r_multiple",
    "stop_tp.tp2_r_multiple": "tp2_r_multiple",
    "stop_tp.time_stop_bars": "time_stop_bars",
    "stop_tp.trailing_stop_atr_multiple": "trailing_stop_atr_multiple",
    "stop_tp.tp1_pct": "tp1_pct",
    "stop_tp.tp2_pct": "tp2_pct",
    "stop_tp.time_stop_vol_multiplier": "time_stop_vol_multiplier",
}


def _extract_protection_settings(params: dict[str, Any]) -> dict[str, Any]:
    """Extract key protection parameters from the sampled flat params dict."""
    out: dict[str, Any] = {}
    for param_key, report_key in _PROTECTION_PARAM_TO_REPORT_KEY.items():
        if param_key in params:
            out[report_key] = params.get(param_key)
    return out


def _infer_protection_diagnostic(metrics: dict[str, Any]) -> str:
    """
    Heuristic to help operators distinguish “likely too-tight protection” vs “likely poor edge”.
    This does not aim for certainty; it’s an interpretability hint for warm-start artifacts.
    """
    stop_out_rate = float(metrics.get("stop_out_rate") or 0.0)
    max_consec_losses = int(metrics.get("max_consecutive_losses") or 0)
    payoff_ratio = float(metrics.get("payoff_ratio") or 0.0)
    expectancy = float(metrics.get("expectancy") or 0.0)
    profit_factor = float(metrics.get("profit_factor") or 0.0)
    median_duration = float(metrics.get("median_trade_duration_sec") or 0.0)

    # “Tight stops / quick exits” style pattern: high stop-outs and consecutive losses
    # while core edge metrics are not completely broken.
    if stop_out_rate >= 0.5 and max_consec_losses >= 3 and profit_factor >= 0.95 and expectancy >= 0:
        # Further bias if exits are typically quick
        if median_duration and median_duration < 120:
            return "likely_too_tight_stops"
        return "likely_too_tight_stops"

    # “Edge is simply poor” pattern.
    if profit_factor < 1.0 or expectancy < 0 or payoff_ratio < 0.8:
        return "likely_poor_edge"

    return "mixed_or_unclear"


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

    warm = getattr(baseline_config, "warm_start", None)
    prioritize_protection_search = bool(getattr(warm, "prioritize_protection_search", False))
    protection_search_bias = str(getattr(warm, "protection_search_bias", "balanced") or "balanced")

    space = get_bounded_space(
        stage4=True,
        stage5=True,
        prioritize_protection_search=prioritize_protection_search,
        protection_search_bias=protection_search_bias,
    )
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
                log.warning(
                    "Warm-start runtime budget reached ({}s); stopping after {} candidates",
                    int(max_runtime_seconds),
                    i,
                )
                timeout_hit = True
                break

        candidate_config = build_config_from_params(baseline_config, params)
        if not candidate_config:
            candidates_invalid += 1
            continue
        try:
            # Backtest-style evaluation with configured fees/slippage from baseline_config.warm_start
            warm = getattr(baseline_config, "warm_start", None)
            fee_bps = float(getattr(warm, "backtest_fee_bps", 6.0)) if warm else 6.0
            slippage_bps = float(getattr(warm, "backtest_slippage_bps", 2.0)) if warm else 2.0
            trades, metrics, replay_meta = run_backtest_on_candles(candidate_config, candles_by_symbol, fee_bps, slippage_bps)
        except Exception as e:
            log.debug("Warm-start backtest candidate {}: {}", i, e)
            candidates_invalid += 1
            continue
        trade_count = int(metrics.get("trade_count") or replay_meta.get("trade_count") or 0)
        replay_trade_counts.append(trade_count)

        protection_settings = _extract_protection_settings(params)
        protection_diagnostic = _infer_protection_diagnostic(metrics)

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
            "protection_settings": protection_settings,
            "protection_diagnostic": protection_diagnostic,
            "oos_metrics": metrics,
            "guardrail_passed": gr.passed,
            "reason_codes": gr.reason_codes,
            "objective_score": score,
        })
        if gr.passed and (best_score_so_far is None or score > best_score_so_far):
            best_score_so_far = score
        log.info(
            "Warm-start candidate %d/%d backtested trades=%s score=%.4f best_so_far=%s",
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

    top_n = 5
    top_rejected_candidates = sorted(
        [r for r in results if not r.get("guardrail_passed", False)],
        key=lambda r: -(float(r.get("objective_score") or 0.0)),
    )[:top_n]

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
        "protection_search_bias": protection_search_bias,
        "prioritize_protection_search": prioritize_protection_search,
        "top_rejected_candidates": [
            {
                "config_id": r.get("config_id"),
                "params": r.get("params"),
                "protection_settings": r.get("protection_settings"),
                "protection_diagnostic": r.get("protection_diagnostic"),
                "oos_metrics": r.get("oos_metrics"),
                "reason_codes": r.get("reason_codes"),
                "objective_score": r.get("objective_score"),
            }
            for r in top_rejected_candidates
        ],
    }
    return best, results, meta
