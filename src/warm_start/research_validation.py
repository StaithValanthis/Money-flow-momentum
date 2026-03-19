"""
Research-quality warm-start validation helpers (Demo-only).

Practical approximations — not paper-perfect PBO/DSR. See field comments and naming.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from src.config.config import Config
from src.warm_start.backtest_engine import run_backtest_on_candles


def _time_windows(
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
    n_windows: int,
) -> List[Dict[str, List[Dict[str, Any]]]]:
    """N contiguous chronological windows (purged: no overlap)."""
    if not candles_by_symbol or n_windows < 2:
        return []
    ref_sym = sorted(candles_by_symbol.keys())[0]
    ref = sorted(candles_by_symbol[ref_sym], key=lambda c: int(c.get("start_ts") or 0))
    if len(ref) < n_windows * 4:
        return []
    t0 = int(ref[0]["start_ts"])
    t1 = int(ref[-1]["start_ts"])
    span = max(t1 - t0, 1)
    windows = []
    for k in range(n_windows):
        a = t0 + int(k * span / n_windows)
        b = t0 + int((k + 1) * span / n_windows)
        d: Dict[str, List[Dict[str, Any]]] = {}
        for sym, lst in candles_by_symbol.items():
            d[sym] = [c for c in lst if a <= int(c.get("start_ts") or 0) < b]
        if any(len(v) >= 3 for v in d.values()):
            windows.append(d)
    return windows


def _quarter_candles(
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, List[Dict[str, Any]]]]:
    """Four chronological quarter windows (regime proxy: different time periods)."""
    ref_sym = sorted(candles_by_symbol.keys())[0]
    ref = sorted(candles_by_symbol[ref_sym], key=lambda c: int(c.get("start_ts") or 0))
    if len(ref) < 20:
        return []
    ts_list = [int(c["start_ts"]) for c in ref]
    t0, t1 = ts_list[0], ts_list[-1]
    span = t1 - t0
    quarters = []
    for q in range(4):
        a = t0 + int(q * span / 4)
        b = t0 + int((q + 1) * span / 4)
        d: Dict[str, List[Dict[str, Any]]] = {}
        for sym, lst in candles_by_symbol.items():
            d[sym] = [c for c in lst if a <= int(c.get("start_ts") or 0) < b]
        if any(len(v) >= 2 for v in d.values()):
            quarters.append(d)
    return quarters


def deep_validate_winner(
    candidate_config: Config,
    baseline_config: Config,
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Multi-window, cost scenarios, regime quarters on the selected candidate only.
    """
    warm = getattr(baseline_config, "warm_start", None)
    out: Dict[str, Any] = {
        "train_metrics": {},
        "validation_metrics": {},
        "stability_score": 1.0,
        "validation_passed": True,
        "metrics_by_cost_scenario": {},
        "cost_sensitivity_passed": True,
        "cost_fragility_score": 0.0,
        "regime_robustness": {},
        "metrics_by_regime": {},
        "regime_fragility_reason": None,
        "rejection_attribution": [],
    }

    base_fee = float(getattr(warm, "backtest_fee_bps", 6.0) or 6.0)
    base_slip = float(getattr(warm, "backtest_slippage_bps", 2.0) or 2.0)
    base_total = base_fee + base_slip

    # --- Multi-window: per-window backtests ---
    if getattr(warm, "use_multi_window_validation", False):
        n = max(2, int(getattr(warm, "validation_split_count", 3) or 3))
        wins = _time_windows(candles_by_symbol, n)
        fold_returns: List[float] = []
        fold_detail: List[Dict[str, Any]] = []
        if len(wins) >= 2:
            for i, wc in enumerate(wins):
                _, wm, _ = run_backtest_on_candles(
                    candidate_config, wc, base_fee, base_slip
                )
                rp = float(wm.get("return_pct") or 0)
                fold_returns.append(rp)
                fold_detail.append({
                    "window": i + 1,
                    "return_pct": round(rp, 4),
                    "profit_factor": round(float(wm.get("profit_factor") or 0), 4),
                    "trades": int(wm.get("trade_count") or 0),
                })
            out["train_metrics"] = fold_detail[0] if fold_detail else {}
            out["validation_metrics"] = {
                "windows": fold_detail,
                "mean_return_pct": round(sum(fold_returns) / len(fold_returns), 4),
                "min_return_pct": round(min(fold_returns), 4),
            }
            pos = sum(1 for x in fold_returns if x > 0)
            mean_abs = sum(abs(x) for x in fold_returns) / len(fold_returns) + 1e-9
            var = sum((x - sum(fold_returns) / len(fold_returns)) ** 2 for x in fold_returns) / len(fold_returns)
            cv = math.sqrt(var) / mean_abs
            out["stability_score"] = round(max(0.0, min(1.0, 1.0 - cv / 2.0)) * (pos / len(fold_returns)), 4)
            min_stab = float(getattr(warm, "min_validation_fold_positive_fraction", 0.34) or 0.34)
            mean_r = sum(fold_returns) / len(fold_returns)
            out["validation_passed"] = (pos / len(fold_returns)) >= min_stab and mean_r > float(
                getattr(warm, "min_validation_mean_return_pct", -2.0) or -2.0
            )
            if not out["validation_passed"]:
                out["rejection_attribution"].append("likely_unstable_across_windows")
        else:
            out["validation_passed"] = True
    else:
        _, m, _ = run_backtest_on_candles(
            candidate_config, candles_by_symbol, base_fee, base_slip
        )
        out["train_metrics"] = {k: m.get(k) for k in ("return_pct", "profit_factor", "trade_count")}

    # --- Cost sensitivity: total bps scenarios ---
    if getattr(warm, "use_cost_sensitivity_check", False):
        scenarios = getattr(warm, "cost_scenarios_bps", None) or [8, 12, 16]
        if not isinstance(scenarios, list):
            scenarios = [8, 12, 16]
        metrics_by: Dict[str, Any] = {}
        profitable_n = 0
        for total_bps in scenarios:
            half = total_bps / 2.0
            _, mm, _ = run_backtest_on_candles(
                candidate_config, candles_by_symbol, half, half
            )
            key = f"total_per_side_{total_bps}bps"
            rp = float(mm.get("return_pct") or 0)
            metrics_by[key] = {
                "return_pct": round(rp, 4),
                "profit_factor": round(float(mm.get("profit_factor") or 0), 4),
                "trade_count": int(mm.get("trade_count") or 0),
            }
            if rp > 0 and float(mm.get("profit_factor") or 0) >= 1.0:
                profitable_n += 1
        out["metrics_by_cost_scenario"] = metrics_by
        need = int(getattr(warm, "min_cost_scenarios_profitable", 2) or 2)
        out["cost_sensitivity_passed"] = profitable_n >= min(need, len(scenarios))
        out["cost_fragility_score"] = round(1.0 - profitable_n / max(len(scenarios), 1), 4)
        if not out["cost_sensitivity_passed"]:
            out["rejection_attribution"].append("likely_cost_fragile")

    # --- Regime quarters ---
    if getattr(warm, "use_regime_validation", False):
        qs = _quarter_candles(candles_by_symbol)
        reg_metrics: Dict[str, Any] = {}
        pos_q = 0
        for i, qc in enumerate(qs):
            _, qm, _ = run_backtest_on_candles(
                candidate_config, qc, base_fee, base_slip
            )
            rp = float(qm.get("return_pct") or 0)
            reg_metrics[f"Q{i+1}"] = {"return_pct": round(rp, 4), "trades": int(qm.get("trade_count") or 0)}
            if rp > 0:
                pos_q += 1
        out["metrics_by_regime"] = reg_metrics
        need_r = int(getattr(warm, "regime_quarters_min_positive", 2) or 2)
        out["regime_robustness"] = {
            "positive_quarters": pos_q,
            "total_quarters": len(qs),
            "fraction": round(pos_q / max(len(qs), 1), 4),
        }
        if pos_q < need_r and len(qs) >= 3:
            out["regime_fragility_reason"] = f"positive_only_{pos_q}_of_{len(qs)}_quarters"
            out["rejection_attribution"].append("likely_regime_fragile")
        else:
            out["regime_fragility_reason"] = None

    return out


def compute_family_overfitting_diagnostics(
    results: List[Dict[str, Any]],
    best_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Practical selection-bias / overfitting proxies (NOT formal PBO).

    - deflated_sharpe_like: subtract sqrt(2*log(N)) from best sharpe_like (Harvey et al. style order of magnitude).
    - overfitting_risk: high when many trials and best far above median.
    """
    n = len([r for r in results if r.get("oos_metrics")])
    sharpe_list: List[float] = []
    obj_list: List[float] = []
    for r in results:
        m = r.get("oos_metrics") or {}
        sharpe_list.append(float(m.get("sharpe_like") or 0))
        obj_list.append(float(r.get("objective_score") or 0))
    if not obj_list:
        return {
            "candidate_family_size": 0,
            "overfitting_risk": 0.0,
            "selection_bias_adjusted_score": 0.0,
            "candidate_family_best_vs_median_gap": 0.0,
            "deflated_sharpe_like": 0.0,
            "note": "approximate_heuristics_not_formal_PBO",
        }

    best_obj = max(obj_list)
    med_obj = sorted(obj_list)[len(obj_list) // 2]
    gap = (best_obj - med_obj) / (abs(med_obj) + 1e-6) if med_obj != 0 else best_obj

    best_sh = max(sharpe_list) if sharpe_list else 0.0
    nn = max(n, 2)
    deflate = math.sqrt(2 * math.log(nn))
    dsr = best_sh - deflate

    # overfitting_risk 0..1: more trials + larger gap => higher
    overfit = min(1.0, (math.log1p(nn) / 5.0) * min(1.0, gap / 3.0))

    adjusted = best_obj - 0.15 * math.log1p(nn)

    return {
        "candidate_family_size": n,
        "overfitting_risk": round(overfit, 4),
        "selection_bias_adjusted_score": round(adjusted, 6),
        "candidate_family_best_vs_median_gap": round(gap, 4),
        "deflated_sharpe_like": round(dsr, 4),
        "note": "approximate_heuristics_not_formal_PBO_or_DSR",
    }


def merge_rejection_attribution(
    protection_diag: str,
    research_attrs: List[str],
) -> str:
    tags = set()
    if protection_diag == "likely_too_tight_stops":
        tags.add("likely_too_tight_protection")
    elif protection_diag == "likely_poor_edge":
        tags.add("likely_poor_edge")
    for a in research_attrs:
        tags.add(a)
    if not tags:
        return "mixed_or_unclear"
    if len(tags) == 1:
        return list(tags)[0]
    return "mixed_or_unclear"


def attribution_from_research_summary(summary: Dict[str, Any]) -> List[str]:
    return list(summary.get("rejection_attribution") or [])


def research_layers_failed(summary: Dict[str, Any], warm: Any) -> List[str]:
    """Which enabled research checks failed (for acceptance messaging)."""
    failed: List[str] = []
    if not summary:
        return failed
    if getattr(warm, "use_multi_window_validation", False) and not summary.get("validation_passed", True):
        failed.append("multi_window")
    if getattr(warm, "use_cost_sensitivity_check", False) and not summary.get("cost_sensitivity_passed", True):
        failed.append("cost_sensitivity")
    if getattr(warm, "use_regime_validation", False):
        mbr = summary.get("metrics_by_regime") or {}
        need_r = int(getattr(warm, "regime_quarters_min_positive", 2) or 2)
        if mbr and len(mbr) >= 3:
            pos_q = sum(1 for v in mbr.values() if isinstance(v, dict) and float(v.get("return_pct") or 0) > 0)
            if pos_q < need_r:
                failed.append("regime")
    return failed
