"""Core and stratified metrics from trades, fills, lifecycle."""

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from src.utils.logging import get_logger

log = get_logger(__name__)


def _pnl_list(trades: list[dict]) -> list[float]:
    return [float(t.get("pnl") or 0) for t in trades]


def _ts_list(trades: list[dict]) -> list[int]:
    return [int(t.get("ts") or 0) for t in trades]


def compute_core_metrics(
    trades: list[dict],
    equity_curve: Optional[list[dict]] = None,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    """Compute core performance metrics from trade list."""
    if not trades:
        return {
            "total_pnl": 0.0,
            "realized_pnl": 0.0,
            "return_pct": 0.0,
            "max_drawdown": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,
            "sharpe_like": 0.0,
            "trade_count": 0,
            "median_trade_duration_sec": 0.0,
            "exposure_time_sec": 0.0,
            "fees_summary": 0.0,
            "slippage_summary": 0.0,
        }
    pnls = np.array(_pnl_list(trades))
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    total_pnl = float(np.sum(pnls))
    n = len(pnls)
    win_rate = float(np.mean(pnls > 0)) if n else 0.0
    expectancy = total_pnl / n if n else 0.0
    avg_win = float(np.mean(wins)) if len(wins) else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) else 0.0
    gross_profit = float(np.sum(wins)) if len(wins) else 0.0
    gross_loss = float(np.abs(np.sum(losses))) if len(losses) else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss else 0.0
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss else 0.0

    return_pct = (total_pnl / initial_equity) * 100.0 if initial_equity else 0.0
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)

    sharpe_like = 0.0
    if n > 1 and np.std(pnls) > 0:
        sharpe_like = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252 * 24 * 12))

    ts = _ts_list(trades)
    median_duration = 0.0
    if len(ts) >= 2:
        durations = np.diff(np.sort(ts)) / 1000.0
        median_duration = float(np.median(durations))
    exposure_sec = (max(ts) - min(ts)) / 1000.0 if len(ts) >= 2 else 0.0

    return {
        "total_pnl": total_pnl,
        "realized_pnl": total_pnl,
        "return_pct": return_pct,
        "max_drawdown": max_dd,
        "expectancy": expectancy,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "sharpe_like": sharpe_like,
        "trade_count": n,
        "median_trade_duration_sec": median_duration,
        "exposure_time_sec": exposure_sec,
        "fees_summary": 0.0,
        "slippage_summary": 0.0,
    }


def compute_stratified_metrics(
    trades: list[dict],
    entry_decisions: Optional[list[dict]] = None,
    by: str = "symbol",
) -> dict[str, dict[str, Any]]:
    """Stratified metrics: by symbol, side, hour, config_id, etc."""
    key_field = by
    buckets: dict[Any, list[dict]] = defaultdict(list)
    for t in trades:
        k = t.get(key_field) or "unknown"
        buckets[k].append(t)
    out = {}
    for k, subset in buckets.items():
        out[str(k)] = compute_core_metrics(subset)
    return out


def compute_score_bucket_metrics(
    entry_decisions: list[dict],
    trades: list[dict],
    n_buckets: int = 10,
) -> dict[str, Any]:
    """Stratify by score decile if score present."""
    if not entry_decisions:
        return {}
    scores = [float(d.get("score") or 0) for d in entry_decisions]
    if not scores:
        return {}
    qs = np.percentile(scores, np.linspace(0, 100, n_buckets + 1))
    bucket_trades: dict[int, list[dict]] = defaultdict(list)
    sym_ts = {(t.get("symbol"), t.get("ts")): t for t in trades}
    for d in entry_decisions:
        s = float(d.get("score") or 0)
        b = int(np.searchsorted(qs[1:], s))
        b = min(b, n_buckets - 1)
        sym = d.get("symbol")
        ts = d.get("ts")
        if sym and ts and (sym, ts) in sym_ts:
            bucket_trades[b].append(sym_ts[(sym, ts)])
    return {f"bucket_{i}": compute_core_metrics(bucket_trades[i]) for i in range(n_buckets) if bucket_trades[i]}


def compute_diagnostic_metrics(
    lifecycle_events: list[dict],
    entry_decisions: list[dict],
    fills: list[dict],
) -> dict[str, Any]:
    """Diagnostic: stop-out rate, TP1/TP2 hit rate, rejection counts, etc."""
    total = len(lifecycle_events)
    by_event: dict[str, int] = defaultdict(int)
    for e in lifecycle_events:
        by_event[str(e.get("event") or "")] += 1
    tp1_hits = by_event.get("tp1_fill_full", 0) + by_event.get("tp1_fill_partial", 0)
    tp2_hits = by_event.get("tp2_fill_full", 0) + by_event.get("tp2_fill_partial", 0)
    stop_outs = by_event.get("protection_repair_success", 0) + by_event.get("stop_moved_breakeven", 0)
    flow_reversal = by_event.get("flow_reversal_exit", 0)
    time_stop = by_event.get("time_stop", 0)
    by_reason: dict[str, int] = defaultdict(int)
    for d in entry_decisions:
        r = str(d.get("reason") or "")
        if r.startswith("rejected:"):
            by_reason[r] += 1
    return {
        "stop_out_rate": stop_outs / total if total else 0.0,
        "tp1_hit_rate": tp1_hits / total if total else 0.0,
        "tp2_hit_rate": tp2_hits / total if total else 0.0,
        "flow_reversal_exit_count": flow_reversal,
        "time_stop_count": time_stop,
        "rejection_reason_counts": dict(by_reason),
        "lifecycle_event_counts": dict(by_event),
        "fill_count": len(fills),
    }


def compute_stage4_metrics(
    lifecycle_events: list[dict],
    signal_snapshots: list[dict],
    entry_decisions: list[dict],
    trades: list[dict],
) -> dict[str, Any]:
    """Stage 4: by exit_reason, regime, threshold_profile, cluster, rejection reasons."""
    import json
    by_exit_reason: dict[str, list[dict]] = defaultdict(list)
    for e in lifecycle_events:
        reason = str(e.get("exit_reason") or e.get("event") or "unknown")
        by_exit_reason[reason].append(e)
    exit_reason_counts = {k: len(v) for k, v in by_exit_reason.items()}

    regime_counts: dict[str, int] = defaultdict(int)
    threshold_profile_counts: dict[str, int] = defaultdict(int)
    cluster_counts: dict[str, int] = defaultdict(int)
    stage4_rejection_counts: dict[str, int] = defaultdict(int)
    for s in signal_snapshots:
        j = s.get("json_features") or "{}"
        try:
            blob = json.loads(j) if isinstance(j, str) else (j or {})
        except Exception:
            blob = {}
        if blob.get("regime_label"):
            regime_counts[str(blob["regime_label"])] += 1
        if blob.get("threshold_profile"):
            threshold_profile_counts[str(blob["threshold_profile"])] += 1
        if blob.get("cluster_id") is not None:
            cluster_counts[f"cluster_{blob['cluster_id']}"] += 1
        if blob.get("rejection_reason"):
            stage4_rejection_counts[str(blob["rejection_reason"])] += 1
    for d in entry_decisions:
        r = str(d.get("reason") or "")
        if "stage4:" in r:
            stage4_rejection_counts[r] += 1

    by_exit_metrics: dict[str, Any] = {}
    for reason, events in by_exit_reason.items():
        by_exit_metrics[reason] = {"event_count": len(events)}

    return {
        "exit_reason_counts": exit_reason_counts,
        "by_exit_reason_metrics": by_exit_metrics,
        "regime_label_counts": dict(regime_counts),
        "threshold_profile_counts": dict(threshold_profile_counts),
        "cluster_id_counts": dict(cluster_counts),
        "stage4_rejection_counts": dict(stage4_rejection_counts),
    }


def compute_stage5_portfolio_metrics(
    entry_decisions: list[dict],
    lifecycle_events: list[dict],
) -> dict[str, Any]:
    """Stage 5: budget/exposure rejection counts, cluster-block counts, allocation resized/method usage."""
    from collections import defaultdict
    stage5_rejection_counts: dict[str, int] = defaultdict(int)
    resized_by_allocation = 0
    allocation_method_usage: dict[str, int] = defaultdict(int)
    for d in entry_decisions:
        r = str(d.get("reason") or "")
        if "stage5:" in r:
            stage5_rejection_counts[r] += 1
        if ":resized" in r:
            resized_by_allocation += 1
        if r.startswith("order_placed") or r.startswith("DRY_RUN_ACCEPTED"):
            for method in ("capped_score_weighted", "cluster_aware", "score_weighted", "equal_risk", "total_risk_budget_cap", "long_risk_budget_cap", "short_risk_budget_cap"):
                if method in r:
                    allocation_method_usage[method] += 1
                    break
    cluster_block_count = sum(1 for d in entry_decisions if "cluster_block" in str(d.get("reason") or "") or "cluster_risk" in str(d.get("reason") or ""))
    budget_block_count = sum(1 for d in entry_decisions if "risk_budget" in str(d.get("reason") or "") or "long_risk_budget" in str(d.get("reason") or "") or "short_risk_budget" in str(d.get("reason") or "") or "total_risk_budget" in str(d.get("reason") or ""))
    return {
        "stage5_rejection_counts": dict(stage5_rejection_counts),
        "cluster_block_count": cluster_block_count,
        "budget_block_count": budget_block_count,
        "resized_by_allocation_count": resized_by_allocation,
        "allocation_method_usage": dict(allocation_method_usage),
    }


def compute_fill_quality_metrics(execution_audit_rows: list[dict]) -> dict[str, Any]:
    """From execution_audit rows: avg entry slippage, median fill delay, size delta stats."""
    if not execution_audit_rows:
        return {}
    slippage_list = [float(r["slippage_bps"]) for r in execution_audit_rows if r.get("slippage_bps") is not None]
    ack_ts = [int(r["ack_ts"]) for r in execution_audit_rows if r.get("ack_ts")]
    fill_ts = [int(r["fill_ts"]) for r in execution_audit_rows if r.get("fill_ts")]
    delays = [fill_ts[i] - ack_ts[i] for i in range(min(len(ack_ts), len(fill_ts)))]
    size_deltas = [float(r["size_delta"]) for r in execution_audit_rows if r.get("size_delta") is not None]
    mismatch_count = sum(1 for r in execution_audit_rows if r.get("mismatch_reason"))
    import numpy as np
    return {
        "avg_entry_slippage_bps": float(np.mean(slippage_list)) if slippage_list else None,
        "median_entry_slippage_bps": float(np.median(slippage_list)) if slippage_list else None,
        "median_ack_to_fill_delay_ms": float(np.median(delays)) if delays else None,
        "execution_drift_count": mismatch_count,
        "size_delta_mean": float(np.mean(size_deltas)) if size_deltas else None,
        "audit_record_count": len(execution_audit_rows),
    }
