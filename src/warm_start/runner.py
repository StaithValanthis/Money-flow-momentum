"""Warm-start calibration runner: detect need, run calibration, seed Demo, record event. Demo-only."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from src.config.config import (
    Config,
    load_config,
    get_effective_operating_mode,
    OPERATING_MODE_DEMO_RESEARCH,
    resolve_bybit_credentials,
)
from src.config.versioning import (
    register_config_version,
    activate_config_version,
    get_active_config_id,
    get_config_version,
    _config_from_artifact_yaml,
)
from src.demo_probation import (
    get_current_probation_status,
    get_probation_record,
    LIFECYCLE_DEMO_PROBATION_FAILED,
)
from src.demo_probation.store import insert_probation_candidate
from src.journal.logger import append_journal_event
from src.lifecycle.logger import append_demo_lifecycle_event
from src.storage.db import Database
from src.storage.migrations import run_stage3_migrations
from src.optimizer.search import run_optimization
from src.evaluation.metrics import compute_core_metrics
from src.evaluation.datasets import load_evaluation_dataset, compute_realized_pnl_by_pairing
from src.utils.logging import get_logger

from src.warm_start.candles import (
    fetch_klines_for_symbol,
    candles_to_synthetic_trades,
    load_cached_candles,
    save_candles_cache,
)
from src.warm_start.strategy_replay import replay_strategy_from_candles
from src.warm_start.candidate_search import run_warm_start_candidate_search
from src.warm_start.acceptance import passes_warm_start_seed_acceptance
from src.warm_start.checkpoint import (
    load_checkpoint,
    save_checkpoint,
    clear_checkpoint,
    archive_checkpoint,
    checkpoint_matches,
    build_checkpoint_payload,
)
from src.evaluation.datasets import get_trade_durations_sec
from src.config.candidate_factory import build_config_from_params
from src.exchange.bybit_client import BybitClient

log = get_logger(__name__)


def is_warm_start_needed(
    demo_db_path: str,
    config: Config,
) -> tuple[bool, str]:
    """
    True when Demo should run warm-start before trading (fresh or insufficient data).
    Returns (needed: bool, reason: str). Only meaningful for operating_mode == demo_research.
    """
    mode = get_effective_operating_mode(config, load_config(None)[1])
    if mode != OPERATING_MODE_DEMO_RESEARCH:
        return False, "operating_mode_not_demo_research"

    warm = getattr(config, "warm_start", None)
    if not warm or not getattr(warm, "enabled", True):
        return False, "warm_start_disabled"
    if not getattr(warm, "auto_seed_demo_on_fresh_install", True):
        return False, "auto_seed_disabled"

    threshold = int(getattr(warm, "min_local_trades_to_skip_warm_start", 50))
    db = Database(demo_db_path)
    try:
        run_stage3_migrations(demo_db_path)
        trades = db.get_trades()
        db.close()
    except Exception as e:
        log.debug(f"Warm-start need check: {e}")
        return True, "no_db_or_error"

    trade_count = len(trades) if trades else 0
    if trade_count >= threshold:
        # Normally we would skip warm-start when there is enough local Demo history, but Demo probation
        # state can require a fresh candidate search even with many trades.
        prob = getattr(config, "demo_probation", None)
        if prob and getattr(prob, "enabled", False):
            override_skip = False
            try:
                active_id = get_active_config_id(demo_db_path)
            except Exception:
                active_id = None
            try:
                prob_status = get_current_probation_status(demo_db_path)
                rec = get_probation_record(active_id, demo_db_path) if active_id else None
            except Exception:
                prob_status = None
                rec = None
            source = None
            if active_id:
                try:
                    cfg_rec = get_config_version(active_id, demo_db_path)
                    source = (cfg_rec or {}).get("source")
                except Exception:
                    source = None
            latest_failed = rec and rec.get("lifecycle_state") == LIFECYCLE_DEMO_PROBATION_FAILED
            no_active_candidate = prob_status is None
            # Override skip when probation has failed or there is no active candidate while running on bootstrap.
            if latest_failed or (no_active_candidate and source == "bootstrap"):
                override_skip = True
            if override_skip:
                log.info(
                    "Warm-start skip overridden: reason={} trade_count={} source={} (fresh candidate search required)",
                    f"sufficient_trades_{trade_count}",
                    trade_count,
                    source or "unknown",
                )
                try:
                    append_journal_event(
                        getattr(config, "artifacts_root", "artifacts"),
                        "WARM_START",
                        "skip_overridden_due_to_probation",
                        instance=getattr(config, "instance_name", None) or "demo",
                        reason="probation_failed_or_no_candidate_with_bootstrap",
                        metrics={"trade_count": trade_count, "threshold": threshold, "source": source or "unknown"},
                    )
                except Exception:
                    pass
                return True, "probation_state_requires_fresh_search"
        return False, f"sufficient_trades_{trade_count}"
    if trade_count > 0:
        return True, f"insufficient_trades_{trade_count}_below_{threshold}"
    return True, "no_local_trades"


def _get_symbols_for_warm_start(config: Config, client: Any) -> list[str]:
    """Resolve symbol list: allowlist or exchange universe, capped by symbols_limit."""
    warm = getattr(config, "warm_start", None)
    limit = int(getattr(warm, "symbols_limit", 50) or 50)
    allowlist = getattr(config.universe, "allowlist", None) or []
    if allowlist:
        return list(allowlist)[:limit]
    try:
        from src.data.universe import UniverseManager
        um = UniverseManager(client, config.universe)
        um.refresh()
        return list(um.symbols)[:limit]
    except Exception as e:
        log.warning(f"Universe refresh for warm-start: {e}")
        return ["BTCUSDT", "ETHUSDT"][:limit]


def _ensure_baseline_in_db(db_path: str, artifact_dir: Path, baseline_config: Config) -> Optional[str]:
    """Register baseline as active in the given DB so optimizer has a baseline_id. Returns config_id."""
    run_stage3_migrations(db_path)
    config_id = register_config_version(
        baseline_config,
        version="warm_start_baseline",
        status="active",
        description="Baseline for warm-start calibration",
        source="bootstrap",
        parent_config_id=None,
        db_path=db_path,
        artifact_dir=artifact_dir,
    )
    db = Database(db_path)
    conn = db._get_conn()
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id != ?", ("archived", config_id))
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("active", config_id))
    conn.commit()
    db.close()
    return config_id


def run_warm_start_calibration(
    demo_db_path: str,
    config_path: Optional[Path] = None,
    artifact_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Run warm-start: fetch candles -> synthetic trades -> temp DB optimization -> seed Demo.
    Only touches Demo DB and Demo artifacts. Returns result dict with success, seed_config_id, reason, etc.
    """
    result: dict[str, Any] = {
        "success": False,
        "skipped": False,
        "reason": "",
        "seed_config_id": None,
        "fallback_used": False,
        "warm_start_used": False,
        "viable_seed_found": False,
        "trade_count_synthetic": 0,
        "optimizer_run_id": None,
        "error": None,
        "engine": None,
        "engine_meta": {},
        "candidate_count_requested": None,
        "candidate_count_evaluated": None,
        "best_candidate_config_id": None,
        "best_candidate_metrics": None,
        "candidates_invalid": None,
        "candidates_replayed": None,
        "no_trades_reason": None,
        "timeout_hit": False,
        "elapsed_seconds": None,
        "window_from_ts_ms": None,
        "window_to_ts_ms": None,
        "symbols": [],
        "timeframe": None,
        "seed_acceptance_passed": None,
        "seed_rejection_reason": None,
        "seed_acceptance_checks": None,
        "ultra_short_trade_fraction": None,
        "median_trade_duration_sec": None,
        "profit_factor": None,
        "payoff_ratio": None,
        "max_drawdown": None,
        "search_until_viable": False,
        "batches_completed": 0,
        "max_batches": None,
        "max_total_runtime_seconds": None,
        "total_candidates_requested": 0,
        "total_candidates_evaluated": 0,
        "total_candidates_replayed": 0,
        "best_rejection_reason_seen": None,
        "search_exhausted": False,
        "require_viable_seed_before_trading": False,
        "stop_out_rate": None,
        "tp1_hit_rate": None,
        "tp2_hit_rate": None,
        "exit_reason_counts": None,
        "max_consecutive_losses": None,
        "run_mode": None,
        "resumed_from_checkpoint": False,
        "checkpoint_cleared_reason": None,
        "best_candidate_params_so_far": None,
    }
    calibration_start = time.time()

    config, env = load_config(config_path)
    mode = get_effective_operating_mode(config, env)
    if mode != OPERATING_MODE_DEMO_RESEARCH:
        result["reason"] = "operating_mode_not_demo_research"
        result["skipped"] = True
        return result

    warm = getattr(config, "warm_start", None)
    if not warm or not getattr(warm, "enabled", True):
        result["reason"] = "warm_start_disabled"
        result["skipped"] = True
        return result

    needed, reason = is_warm_start_needed(demo_db_path, config)
    if not needed:
        result["reason"] = reason
        result["skipped"] = True
        return result

    log.info("Demo initialization started")
    artifact_dir = artifact_dir or Path(config.artifacts_root)
    append_demo_lifecycle_event(
        config.artifacts_root, getattr(config, "instance_name", None),
        "DEMO_INIT", "started",
    )
    to_ts_ms = int(time.time() * 1000)
    lookback_days = int(getattr(warm, "lookback_days", 30))
    from_ts_ms = to_ts_ms - lookback_days * 86400 * 1000
    interval = str(getattr(warm, "timeframe", "5"))
    candle_source = getattr(warm, "candle_source", "exchange")
    require_profitable = getattr(warm, "require_profitable_seed", True)
    fallback = getattr(warm, "fallback_to_safe_seed_on_failure", True)

    # Resolve symbols
    symbols: list[str] = []
    client = None
    try:
        api_key, api_secret, _, _ = resolve_bybit_credentials(env, "demo")
        if not api_key or not api_secret:
            result["error"] = "demo_api_credentials_missing"
            result["reason"] = "cannot_fetch_candles"
            if fallback:
                _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
            return result
        client = BybitClient(api_key=api_key, api_secret=api_secret, demo=True)
        symbols = _get_symbols_for_warm_start(config, client)
        log.info("Warm-start universe resolved: {} symbols", len(symbols))
    except Exception as e:
        log.exception("Warm-start client/symbols")
        result["error"] = str(e)
        result["reason"] = "client_error"
        if fallback:
            _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
        return result

    if not symbols:
        result["reason"] = "no_symbols"
        if fallback:
            _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
        return result

    # Load or fetch candles
    candles_by_symbol: dict[str, list[dict]] = {}
    if candle_source in ("local", "local_or_exchange"):
        candles_by_symbol = load_cached_candles(config.artifacts_root, symbols)
    if not candles_by_symbol and candle_source in ("exchange", "local_or_exchange") and client:
        for symbol in symbols:
            try:
                cand = fetch_klines_for_symbol(client, symbol, interval, from_ts_ms, to_ts_ms)
                if cand:
                    candles_by_symbol[symbol] = cand
            except Exception as e:
                log.debug(f"Fetch klines {symbol}: {e}")
        if candles_by_symbol:
            save_candles_cache(config.artifacts_root, candles_by_symbol)
            log.info("Warm-start candles fetched: {} symbols", len(candles_by_symbol))
    if candles_by_symbol:
        log.info("Warm-start candles ready: {} symbols", len(candles_by_symbol))

    if not candles_by_symbol:
        result["reason"] = "no_candle_data"
        result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
        if fallback:
            log.info("Warm-start fallback: no candle data; activating conservative seed")
            _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
        return result

    result["window_from_ts_ms"] = from_ts_ms
    result["window_to_ts_ms"] = to_ts_ms
    result["symbols"] = sorted(candles_by_symbol.keys())
    result["timeframe"] = interval

    baseline_config = config
    n_samples = int(getattr(warm, "n_samples", 8))
    max_runtime_seconds = int(getattr(warm, "max_runtime_seconds", 300) or 0) or None
    search_until_viable = bool(getattr(warm, "search_until_viable", False))
    batch_n_samples = int(getattr(warm, "batch_n_samples", 8))
    max_batches = int(getattr(warm, "max_batches", 10))
    max_total_runtime_seconds = int(getattr(warm, "max_total_runtime_seconds", 1800) or 0) or 1800
    allow_fallback_if_no_viable_seed = bool(getattr(warm, "allow_fallback_if_no_viable_seed", True))
    require_viable_seed_before_trading = bool(getattr(warm, "require_viable_seed_before_trading", False))

    result["search_until_viable"] = search_until_viable
    result["require_viable_seed_before_trading"] = require_viable_seed_before_trading
    if search_until_viable:
        result["max_batches"] = max_batches
        result["max_total_runtime_seconds"] = max_total_runtime_seconds

    # --- Iterative batch search (search_until_viable=True) ---
    if search_until_viable:
        total_requested = 0
        total_replayed = 0
        total_invalid = 0
        best_rejection_reason_seen: Optional[str] = None
        # Small operator-friendly slice of the most promising rejected candidates.
        top_rejected_candidates_all: list[dict[str, Any]] = []
        start_batch_index = 0
        symbols_sorted = sorted(candles_by_symbol.keys())
        cp = load_checkpoint(artifact_dir)
        if cp and checkpoint_matches(config, cp, symbols_sorted, interval, lookback_days):
            result["run_mode"] = "resumed"
            result["resumed_from_checkpoint"] = True
            total_requested = int(cp.get("total_candidates_requested") or 0)
            total_replayed = int(cp.get("total_candidates_evaluated") or 0)
            total_invalid = int(cp.get("total_candidates_invalid") or 0)
            best_rejection_reason_seen = cp.get("best_rejection_reason_seen")
            start_batch_index = int(cp.get("last_completed_batch_index") or -1) + 1
            result["best_candidate_metrics"] = cp.get("best_candidate_metrics_so_far")
            result["best_candidate_params_so_far"] = cp.get("best_candidate_params_so_far")
            result["best_rejection_reason_seen"] = best_rejection_reason_seen
            log.info("Resuming initialization from checkpoint")
            append_demo_lifecycle_event(
                config.artifacts_root, getattr(config, "instance_name", None),
                "WARMUP", "resumed_from_checkpoint",
            )
        elif cp:
            result["run_mode"] = "restarted_config_changed"
            result["resumed_from_checkpoint"] = False
            log.info("Warm-start config or symbols changed; starting fresh search (checkpoint not resumed)")
            clear_checkpoint(artifact_dir)
        else:
            result["run_mode"] = "fresh"
            result["resumed_from_checkpoint"] = False

        log.info("Searching for passable startup config")
        for batch_num in range(start_batch_index, max_batches):
            elapsed = time.time() - calibration_start
            if elapsed >= max_total_runtime_seconds:
                log.info(
                    "Warm-start total runtime budget reached ({}s); stopping after {} batches",
                    int(max_total_runtime_seconds),
                    batch_num,
                )
                break
            remaining = max(60, int(max_total_runtime_seconds - elapsed))
            log.info(
                "Warm-start batch {}/{} (n_samples={}, remaining_runtime={}s)",
                batch_num + 1,
                max_batches,
                batch_n_samples,
                remaining,
            )
            try:
                best, all_results, search_meta = run_warm_start_candidate_search(
                    baseline_config,
                    candles_by_symbol,
                    n_samples=batch_n_samples,
                    min_trades_guardrail=5,
                    require_profitable=require_profitable,
                    max_runtime_seconds=remaining,
                    start_time=calibration_start,
                )
            except Exception as e:
                log.warning("Warm-start batch {} failed: {}", batch_num + 1, e)
                result["error"] = str(e)
                total_invalid += batch_n_samples
                total_requested += batch_n_samples
                result["batches_completed"] = batch_num + 1
                result["total_candidates_requested"] = total_requested
                result["total_candidates_evaluated"] = total_replayed
                result["total_candidates_replayed"] = total_replayed
                result["candidates_invalid"] = total_invalid
                continue

            # Merge top rejected candidates across batches (dedupe by config_id).
            try:
                batch_top_rejected = search_meta.get("top_rejected_candidates") or []
                if batch_top_rejected:
                    top_rejected_candidates_all.extend(batch_top_rejected)
                    dedup: dict[str, dict[str, Any]] = {}
                    for tr in top_rejected_candidates_all:
                        cid = tr.get("config_id")
                        if cid:
                            dedup[cid] = tr
                    top_rejected_candidates_all = sorted(
                        dedup.values(),
                        key=lambda r: -(float(r.get("objective_score") or 0.0)),
                    )[:5]
            except Exception:
                pass
            batch_requested = search_meta.get("candidate_count_requested", batch_n_samples)
            batch_replayed = search_meta.get("candidates_replayed", 0)
            batch_invalid = search_meta.get("candidates_invalid", 0)
            total_requested += batch_requested
            total_replayed += batch_replayed
            total_invalid += batch_invalid
            result["batches_completed"] = batch_num + 1
            result["total_candidates_requested"] = total_requested
            result["total_candidates_evaluated"] = total_replayed
            result["total_candidates_replayed"] = total_replayed
            result["candidates_invalid"] = total_invalid
            result["no_trades_reason"] = search_meta.get("no_trades_reason")
            result["timeout_hit"] = search_meta.get("timeout_hit", False)
            result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
            if best is not None:
                result["best_candidate_params_so_far"] = best.get("params")
                result["best_candidate_metrics"] = best.get("oos_metrics")
                result["best_candidate_protection_settings_so_far"] = best.get("protection_settings")
                result["best_candidate_protection_diagnostic_so_far"] = best.get("protection_diagnostic")

            # Persist checkpoint after each batch so run can resume later
            result["candidates_invalid"] = total_invalid
            save_checkpoint(artifact_dir, build_checkpoint_payload(result, config, symbols_sorted, interval, lookback_days))

            if best is not None:
                winning_config = build_config_from_params(baseline_config, best["params"])
                if winning_config:
                    try:
                        from src.warm_start.backtest_engine import run_backtest_on_candles
                        warm_cfg = getattr(baseline_config, "warm_start", None)
                        fee_bps = float(getattr(warm_cfg, "backtest_fee_bps", 6.0)) if warm_cfg else 6.0
                        slip_bps = float(getattr(warm_cfg, "backtest_slippage_bps", 2.0)) if warm_cfg else 2.0
                        winner_trades, _, _ = run_backtest_on_candles(winning_config, candles_by_symbol, fee_bps, slip_bps)
                        durations_sec = get_trade_durations_sec(winner_trades)
                    except Exception as e:
                        log.warning("Warm-start re-backtest for acceptance failed: {}", e)
                        durations_sec = []
                    metrics = best.get("oos_metrics") or {}
                    accepted, rejection_reason, acceptance_checks = passes_warm_start_seed_acceptance(
                        metrics,
                        config,
                        durations_sec=durations_sec,
                        fees_summary=float(metrics.get("fees_summary") or 0),
                        slippage_summary=float(metrics.get("slippage_summary") or 0),
                    )
                    if rejection_reason:
                        best_rejection_reason_seen = rejection_reason
                    result["seed_acceptance_checks"] = acceptance_checks
                    result["ultra_short_trade_fraction"] = acceptance_checks.get("ultra_short_trade_fraction")
                    result["median_trade_duration_sec"] = acceptance_checks.get("median_trade_duration_sec")
                    result["profit_factor"] = acceptance_checks.get("profit_factor")
                    result["payoff_ratio"] = acceptance_checks.get("payoff_ratio")
                    result["max_drawdown"] = acceptance_checks.get("max_drawdown")
                    result["seed_acceptance_passed"] = accepted
                    result["seed_rejection_reason"] = rejection_reason if not accepted else None
                    result["best_rejection_reason_seen"] = best_rejection_reason_seen
                    result["best_candidate_metrics"] = metrics
                    result["stop_out_rate"] = metrics.get("stop_out_rate")
                    result["tp1_hit_rate"] = metrics.get("tp1_hit_rate")
                    result["tp2_hit_rate"] = metrics.get("tp2_hit_rate")
                    result["exit_reason_counts"] = metrics.get("exit_reason_counts")
                    result["max_consecutive_losses"] = metrics.get("max_consecutive_losses")
                    result["best_candidate_protection_settings"] = best.get("protection_settings")
                    result["best_candidate_protection_diagnostic"] = best.get("protection_diagnostic")
                    result["top_rejected_candidates"] = top_rejected_candidates_all

                    if accepted:
                        result["viable_seed_found"] = True
                        result["engine"] = "parameter_aware_protection_backtest"
                        log.info("Passable config found; activating Demo seed")
                        run_stage3_migrations(demo_db_path)
                        demo_artifact_dir = Path(artifact_dir) / "configs"
                        demo_artifact_dir.mkdir(parents=True, exist_ok=True)
                        new_id = register_config_version(
                            winning_config,
                            version="warm_start_seed",
                            status="candidate",
                            description="Warm-start calibration seed (parameter-aware replay)",
                            source="warm_start",
                            parent_config_id=None,
                            db_path=demo_db_path,
                            artifact_dir=demo_artifact_dir,
                        )
                        if activate_config_version(new_id, demo_db_path, reason="warm_start", manual=False):
                            result["success"] = True
                            result["seed_config_id"] = new_id
                            result["warm_start_used"] = True
                            result["reason"] = "warm_start_seeded"
                            result["engine_meta"] = {
                                "candidate_count_evaluated": total_replayed,
                                "best_candidate_config_id": new_id,
                                "best_candidate_metrics": best.get("oos_metrics"),
                                "batches_completed": batch_num + 1,
                                "window_from_ts_ms": from_ts_ms,
                                "window_to_ts_ms": to_ts_ms,
                                "symbols": result["symbols"],
                                "timeframe": interval,
                            }
                            result["best_candidate_config_id"] = new_id
                            result["candidate_count_evaluated"] = total_replayed
                            result["fallback_used"] = False
                            result["trade_count_synthetic"] = int((best.get("oos_metrics") or {}).get("trade_count") or 0)
                            result["best_candidate_protection_settings"] = best.get("protection_settings")
                            result["best_candidate_protection_diagnostic"] = best.get("protection_diagnostic")
                            result["top_rejected_candidates"] = top_rejected_candidates_all
                            log.info("Warm-start final seed activated: config_id={}", new_id)
                            append_demo_lifecycle_event(
                                config.artifacts_root, getattr(config, "instance_name", None),
                                "WARMUP", "passable_config_found", config_id=new_id,
                            )
                            try:
                                append_journal_event(
                                    config.artifacts_root,
                                    "WARMUP",
                                    "protection_candidate_accepted",
                                    instance=getattr(config, "instance_name", None) or "demo",
                                    config_id=new_id,
                                    status=best.get("protection_diagnostic"),
                                    metrics={
                                        "protection_settings": best.get("protection_settings") or {},
                                    },
                                    reason=best.get("protection_diagnostic") or "accepted",
                                )
                            except Exception:
                                pass
                            _register_probation_candidate_if_enabled(config, new_id, demo_db_path)
                            result["checkpoint_cleared_reason"] = "viable_seed_found"
                            archive_checkpoint(artifact_dir)
                            _write_warm_start_artifact(artifact_dir, result, config)
                            append_demo_lifecycle_event(
                                config.artifacts_root, getattr(config, "instance_name", None),
                                "DEMO_INIT", "init_complete_success", config_id=new_id,
                            )
                            return result
                    else:
                        log.info(
                            "Warm-start batch {} winner rejected by acceptance: {}",
                            batch_num + 1,
                            rejection_reason,
                        )
                        append_demo_lifecycle_event(
                            config.artifacts_root, getattr(config, "instance_name", None),
                            "WARMUP", "passable_config_rejected", reason=rejection_reason or "acceptance_failed",
                        )
            else:
                if search_meta.get("no_trades_reason"):
                    best_rejection_reason_seen = search_meta.get("no_trades_reason")

        result["search_exhausted"] = True
        result["viable_seed_found"] = False
        result["best_rejection_reason_seen"] = best_rejection_reason_seen
        result["top_rejected_candidates"] = top_rejected_candidates_all
        result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
        result["reason"] = "no_viable_seed_search_exhausted"
        result["engine"] = "parameter_aware_protection_backtest"
        result["checkpoint_cleared_reason"] = "search_exhausted"
        archive_checkpoint(artifact_dir)
        append_demo_lifecycle_event(
            config.artifacts_root, getattr(config, "instance_name", None),
            "WARMUP", "search_exhausted", reason=best_rejection_reason_seen or "no_viable_seed",
        )
        if require_viable_seed_before_trading and not allow_fallback_if_no_viable_seed:
            log.info("No passable config found yet; Demo trading will not start")
            append_demo_lifecycle_event(
                config.artifacts_root, getattr(config, "instance_name", None),
                "DEMO_INIT", "no_passable_config_found",
            )
        else:
            log.info(
                "Warm-start search exhausted after {} batches; no viable seed",
                result["batches_completed"],
            )
        if allow_fallback_if_no_viable_seed:
            log.info("Warm-start fallback: search exhausted without viable seed; activating conservative seed")
            _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
            append_demo_lifecycle_event(
                config.artifacts_root, getattr(config, "instance_name", None),
                "DEMO_INIT", "init_complete_success", reason="fallback_seed",
            )
        else:
            _write_warm_start_artifact(artifact_dir, result, config)
            append_demo_lifecycle_event(
                config.artifacts_root, getattr(config, "instance_name", None),
                "DEMO_INIT", "init_complete_failure", reason="search_exhausted_no_fallback",
            )
        return result

    # --- Single-batch path (search_until_viable=False): parameter-aware replay ---
    n_samples_use = n_samples
    max_runtime_use = max_runtime_seconds
    log.info(
        "Warm-start candidate search started (n_samples={}, max_runtime_seconds={})",
        n_samples_use,
        max_runtime_use,
    )
    try:
        best, all_results, search_meta = run_warm_start_candidate_search(
            baseline_config,
            candles_by_symbol,
            n_samples=n_samples_use,
            min_trades_guardrail=5,
            require_profitable=require_profitable,
            max_runtime_seconds=max_runtime_use,
            start_time=calibration_start,
        )
        result["candidate_count_requested"] = search_meta.get("candidate_count_requested", n_samples_use)
        result["candidates_invalid"] = search_meta.get("candidates_invalid")
        result["candidates_replayed"] = search_meta.get("candidates_replayed")
        result["candidate_count_evaluated"] = search_meta.get("candidates_replayed")
        result["no_trades_reason"] = search_meta.get("no_trades_reason")
        result["timeout_hit"] = search_meta.get("timeout_hit", False)
        result["elapsed_seconds"] = search_meta.get("elapsed_seconds")
        if result["elapsed_seconds"] is None:
            result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
        result["top_rejected_candidates"] = search_meta.get("top_rejected_candidates") or []
        if best is not None:
            winning_config = build_config_from_params(baseline_config, best["params"])
            if winning_config:
                # Strict seed acceptance: use backtest trades (with exit_reason) for durations
                try:
                    from src.warm_start.backtest_engine import run_backtest_on_candles
                    warm_cfg = getattr(baseline_config, "warm_start", None)
                    fee_bps = float(getattr(warm_cfg, "backtest_fee_bps", 6.0)) if warm_cfg else 6.0
                    slip_bps = float(getattr(warm_cfg, "backtest_slippage_bps", 2.0)) if warm_cfg else 2.0
                    winner_trades, _, _ = run_backtest_on_candles(winning_config, candles_by_symbol, fee_bps, slip_bps)
                    durations_sec = get_trade_durations_sec(winner_trades)
                except Exception as e:
                    log.warning("Warm-start re-backtest for acceptance failed: {}", e)
                    durations_sec = []
                metrics = best.get("oos_metrics") or {}
                accepted, rejection_reason, acceptance_checks = passes_warm_start_seed_acceptance(
                    metrics,
                    config,
                    durations_sec=durations_sec,
                    fees_summary=float(metrics.get("fees_summary") or 0),
                    slippage_summary=float(metrics.get("slippage_summary") or 0),
                )
                result["seed_acceptance_checks"] = acceptance_checks
                result["ultra_short_trade_fraction"] = acceptance_checks.get("ultra_short_trade_fraction")
                result["median_trade_duration_sec"] = acceptance_checks.get("median_trade_duration_sec")
                result["profit_factor"] = acceptance_checks.get("profit_factor")
                result["payoff_ratio"] = acceptance_checks.get("payoff_ratio")
                result["max_drawdown"] = acceptance_checks.get("max_drawdown")

                if not accepted:
                    result["seed_acceptance_passed"] = False
                    result["seed_rejection_reason"] = rejection_reason
                    result["reason"] = "seed_rejected_by_acceptance"
                    result["best_candidate_config_id"] = None
                    result["best_candidate_metrics"] = metrics
                    result["stop_out_rate"] = metrics.get("stop_out_rate")
                    result["tp1_hit_rate"] = metrics.get("tp1_hit_rate")
                    result["tp2_hit_rate"] = metrics.get("tp2_hit_rate")
                    result["exit_reason_counts"] = metrics.get("exit_reason_counts")
                    result["max_consecutive_losses"] = metrics.get("max_consecutive_losses")
                    result["best_candidate_protection_settings"] = best.get("protection_settings")
                    result["best_candidate_protection_diagnostic"] = best.get("protection_diagnostic")
                    result["candidate_count_evaluated"] = len(all_results)
                    result["engine"] = "parameter_aware_protection_backtest"
                    result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
                    result["viable_seed_found"] = False
                    log.info(
                        "Warm-start replay winner rejected by seed acceptance: {}",
                        rejection_reason,
                    )
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "WARMUP", "passable_config_rejected", reason=rejection_reason or "acceptance_failed",
                    )
                    try:
                        append_journal_event(
                            config.artifacts_root,
                            "WARMUP",
                            "protection_candidate_rejected",
                            instance=getattr(config, "instance_name", None) or "demo",
                            reason=rejection_reason or "acceptance_failed",
                            status=best.get("protection_diagnostic"),
                            metrics={
                                "protection_settings": best.get("protection_settings") or {},
                                "protection_diagnostic": best.get("protection_diagnostic"),
                            },
                        )
                    except Exception:
                        pass
                    if fallback:
                        log.info("Warm-start fallback: replay winner rejected; activating conservative seed")
                        _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
                        append_demo_lifecycle_event(
                            config.artifacts_root, getattr(config, "instance_name", None),
                            "DEMO_INIT", "init_complete_success", reason="fallback_seed",
                        )
                    else:
                        append_demo_lifecycle_event(
                            config.artifacts_root, getattr(config, "instance_name", None),
                            "DEMO_INIT", "init_complete_failure", reason="seed_rejected_no_fallback",
                        )
                    _write_warm_start_artifact(artifact_dir, result, config)
                    return result

                result["seed_acceptance_passed"] = True
                result["seed_rejection_reason"] = None
                result["viable_seed_found"] = True
                log.info("Warm-start best candidate selected and passed seed acceptance; activating seed")
                run_stage3_migrations(demo_db_path)
                demo_artifact_dir = Path(artifact_dir) / "configs"
                demo_artifact_dir.mkdir(parents=True, exist_ok=True)
                new_id = register_config_version(
                    winning_config,
                    version="warm_start_seed",
                    status="candidate",
                    description="Warm-start calibration seed (parameter-aware replay)",
                    source="warm_start",
                    parent_config_id=None,
                    db_path=demo_db_path,
                    artifact_dir=demo_artifact_dir,
                )
                if activate_config_version(new_id, demo_db_path, reason="warm_start", manual=False):
                    result["success"] = True
                    result["seed_config_id"] = new_id
                    result["warm_start_used"] = True
                    result["reason"] = "warm_start_seeded"
                    result["engine"] = "parameter_aware_protection_backtest"
                    _register_probation_candidate_if_enabled(config, new_id, demo_db_path)
                    result["engine_meta"] = {
                        "candidate_count_evaluated": len(all_results),
                        "best_candidate_config_id": new_id,
                        "best_candidate_metrics": best.get("oos_metrics"),
                        "candidates_invalid": search_meta.get("candidates_invalid"),
                        "candidates_replayed": search_meta.get("candidates_replayed"),
                        "no_trades_reason": search_meta.get("no_trades_reason"),
                        "window_from_ts_ms": from_ts_ms,
                        "window_to_ts_ms": to_ts_ms,
                        "symbols": result["symbols"],
                        "timeframe": interval,
                    }
                    result["candidate_count_evaluated"] = len(all_results)
                    result["best_candidate_config_id"] = new_id
                    result["best_candidate_metrics"] = best.get("oos_metrics")
                    result["fallback_used"] = False
                    result["trade_count_synthetic"] = int((best.get("oos_metrics") or {}).get("trade_count") or 0)
                    result["stop_out_rate"] = (best.get("oos_metrics") or {}).get("stop_out_rate")
                    result["tp1_hit_rate"] = (best.get("oos_metrics") or {}).get("tp1_hit_rate")
                    result["tp2_hit_rate"] = (best.get("oos_metrics") or {}).get("tp2_hit_rate")
                    result["exit_reason_counts"] = (best.get("oos_metrics") or {}).get("exit_reason_counts")
                    result["max_consecutive_losses"] = (best.get("oos_metrics") or {}).get("max_consecutive_losses")
                    result["best_candidate_protection_settings"] = best.get("protection_settings")
                    result["best_candidate_protection_diagnostic"] = best.get("protection_diagnostic")
                    if result.get("timeout_hit"):
                        result["reason"] = "warm_start_seeded_timeout_best_so_far"
                    result["viable_seed_found"] = True
                    log.info("Warm-start final seed activated: config_id={}", new_id)
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "WARMUP", "passable_config_found", config_id=new_id,
                    )
                    try:
                        append_journal_event(
                            config.artifacts_root,
                            "WARMUP",
                            "protection_candidate_accepted",
                            instance=getattr(config, "instance_name", None) or "demo",
                            config_id=new_id,
                            status=result.get("best_candidate_protection_diagnostic"),
                            metrics={
                                "protection_settings": result.get("best_candidate_protection_settings") or {},
                                "protection_diagnostic": result.get("best_candidate_protection_diagnostic"),
                            },
                            reason=result.get("best_candidate_protection_diagnostic") or "accepted",
                        )
                    except Exception:
                        pass
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "DEMO_INIT", "init_complete_success", config_id=new_id,
                    )
                    _write_warm_start_artifact(artifact_dir, result, config)
                    return result
    except Exception as e:
        log.warning(
            "Parameter-aware warm-start failed, falling back to single-replay path: {}",
            e,
        )
        result["error"] = str(e)
        result["reason"] = "parameter_aware_failed_fallback"

    # --- Fallback: single replay + temp DB optimizer (legacy path) ---
    try:
        synthetic_trades, engine_meta = replay_strategy_from_candles(config, candles_by_symbol)
        result["engine"] = engine_meta.get("engine", "strategy_replay")
        result["engine_meta"] = engine_meta
    except Exception as e:
        log.exception("Warm-start strategy replay failed; falling back to synthetic momentum proxy")
        result["error"] = str(e)
        synthetic_trades = candles_to_synthetic_trades(candles_by_symbol)
        result["engine"] = "synthetic_momentum_proxy"
        result["engine_meta"] = {
            "note": "strategy_replay_failed_fallback_to_synthetic",
        }

    result["trade_count_synthetic"] = len([t for t in synthetic_trades if t.get("pnl") is not None])
    result["candidate_count_evaluated"] = 0
    result["candidates_invalid"] = 0
    result["candidates_replayed"] = 0
    if result["trade_count_synthetic"] == 0:
        result["no_trades_reason"] = "single_replay_produced_zero_trades"

    if result["trade_count_synthetic"] < 20:
        log.warning("Warm-start fallback: very few synthetic trades; calibration weak")
        result["reason"] = "insufficient_synthetic_trades"
        result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
        if fallback:
            log.info("Warm-start fallback: activating conservative seed")
            _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
        return result

    # Temp DB + baseline + inject trades + run optimizer
    with tempfile.TemporaryDirectory(prefix="warm_start_") as tmpdir:
        temp_db = str(Path(tmpdir) / "temp.db")
        temp_artifacts = Path(tmpdir) / "artifacts"
        temp_artifacts.mkdir(parents=True, exist_ok=True)
        baseline_config, _ = load_config(config_path)
        if not isinstance(baseline_config, Config):
            baseline_config = config
        baseline_id = _ensure_baseline_in_db(temp_db, temp_artifacts, baseline_config)
        if not baseline_id:
            result["error"] = "baseline_register_failed"
            if fallback:
                _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
            return result

        db = Database(temp_db)
        for t in synthetic_trades:
            db.insert_trade(
                ts=t["ts"],
                symbol=t["symbol"],
                side=t["side"],
                qty=t["qty"],
                price=t["price"],
                order_id=t.get("order_id") or "",
                order_link_id=t.get("order_link_id") or "",
                pnl=t.get("pnl"),
                config_id=baseline_id,
            )
        db.close()

        try:
            opt_out = run_optimization(
                db_path=temp_db,
                config_id=baseline_id,
                from_ts=from_ts_ms,
                to_ts=to_ts_ms,
                n_samples=min(30, max(10, result["trade_count_synthetic"] // 10)),
                train_pct=0.5,
                val_pct=0.25,
                test_pct=0.25,
                artifact_dir=temp_artifacts,
            )
        except Exception as e:
            log.exception("Warm-start optimizer run")
            result["error"] = str(e)
            result["reason"] = "optimizer_error"
            if fallback:
                _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
            return result

        best_id = opt_out.get("best_candidate_config_id")
        result["optimizer_run_id"] = opt_out.get("run_id")

        legacy_data = None
        if best_id and require_profitable:
            legacy_data = load_evaluation_dataset(temp_db, from_ts=from_ts_ms, to_ts=to_ts_ms, config_id=best_id)
            trades = compute_realized_pnl_by_pairing(legacy_data["trades"])
            metrics = compute_core_metrics(trades)
            if float(metrics.get("total_pnl") or 0) <= 0:
                best_id = None
                result["reason"] = "no_profitable_seed"
                legacy_data = None

        if best_id:
            # Run seed acceptance on optimizer winner (same as primary path) and populate report fields
            data = legacy_data or load_evaluation_dataset(temp_db, from_ts=from_ts_ms, to_ts=to_ts_ms, config_id=best_id)
            trades_raw = data["trades"]
            paired = compute_realized_pnl_by_pairing(trades_raw)
            metrics = compute_core_metrics(paired)
            durations_sec = get_trade_durations_sec(trades_raw)
            accepted, rejection_reason, acceptance_checks = passes_warm_start_seed_acceptance(
                metrics,
                config,
                durations_sec=durations_sec,
                fees_summary=float(metrics.get("fees_summary") or 0),
                slippage_summary=float(metrics.get("slippage_summary") or 0),
            )
            result["seed_acceptance_checks"] = acceptance_checks
            result["ultra_short_trade_fraction"] = acceptance_checks.get("ultra_short_trade_fraction")
            result["median_trade_duration_sec"] = acceptance_checks.get("median_trade_duration_sec")
            result["profit_factor"] = acceptance_checks.get("profit_factor")
            result["payoff_ratio"] = acceptance_checks.get("payoff_ratio")
            result["max_drawdown"] = acceptance_checks.get("max_drawdown")
            result["seed_acceptance_passed"] = accepted
            result["seed_rejection_reason"] = rejection_reason if not accepted else None

            if not accepted:
                result["reason"] = "seed_rejected_by_acceptance"
                result["best_candidate_metrics"] = metrics
                result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
                log.info(
                    "Warm-start legacy path: optimizer winner rejected by seed acceptance: {}",
                    rejection_reason,
                )
                append_demo_lifecycle_event(
                    config.artifacts_root, getattr(config, "instance_name", None),
                    "WARMUP", "passable_config_rejected", reason=rejection_reason or "acceptance_failed",
                )
                if fallback:
                    log.info("Warm-start fallback: legacy winner rejected; activating conservative seed")
                    _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "DEMO_INIT", "init_complete_success", reason="fallback_seed",
                    )
                else:
                    append_demo_lifecycle_event(
                        config.artifacts_root, getattr(config, "instance_name", None),
                        "DEMO_INIT", "init_complete_failure", reason="seed_rejected_no_fallback",
                    )
                    _write_warm_start_artifact(artifact_dir, result, config)
                return result

            # Accepted: copy best config from temp DB to Demo DB and activate
            rec = get_config_version(best_id, temp_db)
            if not rec or not rec.get("artifact_path"):
                result["reason"] = "best_config_artifact_missing"
                if fallback:
                    _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
                return result
            art_path = Path(rec["artifact_path"])
            if not art_path.exists():
                result["reason"] = "best_config_artifact_not_found"
                if fallback:
                    _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
                return result
            seed_config = _config_from_artifact_yaml(art_path)
            if not seed_config:
                result["reason"] = "best_config_invalid"
                if fallback:
                    _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
                return result
            demo_artifact_dir = Path(artifact_dir) / "configs"
            demo_artifact_dir.mkdir(parents=True, exist_ok=True)
            new_id = register_config_version(
                seed_config,
                version="warm_start_seed",
                status="candidate",
                description="Warm-start calibration seed",
                source="warm_start",
                parent_config_id=None,
                db_path=demo_db_path,
                artifact_dir=demo_artifact_dir,
            )
            if activate_config_version(new_id, demo_db_path, reason="warm_start", manual=False):
                result["success"] = True
                result["seed_config_id"] = new_id
                result["warm_start_used"] = True
                result["reason"] = "warm_start_seeded"
                result["best_candidate_config_id"] = new_id
                result["best_candidate_metrics"] = metrics
                result["viable_seed_found"] = True
                append_demo_lifecycle_event(
                    config.artifacts_root, getattr(config, "instance_name", None),
                    "WARMUP", "passable_config_found", config_id=new_id,
                )
                append_demo_lifecycle_event(
                    config.artifacts_root, getattr(config, "instance_name", None),
                    "DEMO_INIT", "init_complete_success", config_id=new_id,
                )
                _register_probation_candidate_if_enabled(config, new_id, demo_db_path)
                _write_warm_start_artifact(artifact_dir, result, config)
                return result
            result["reason"] = "activation_failed"
        else:
            result["reason"] = result.get("reason") or "no_acceptable_candidate"
            result["viable_seed_found"] = False
            result["seed_acceptance_passed"] = False
            result["seed_rejection_reason"] = result["reason"]
            result["seed_acceptance_checks"] = {}
            result["ultra_short_trade_fraction"] = None
            result["median_trade_duration_sec"] = None
            result["profit_factor"] = None
            result["payoff_ratio"] = None
            result["max_drawdown"] = None

    result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
    if fallback:
        log.info(
            "Warm-start fallback: no acceptable candidate; activating conservative seed (reason={})",
            result.get("reason"),
        )
        _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
    return result
def run_demo_init(
    demo_db_path: str,
    config_path: Optional[Path] = None,
    artifact_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Single Demo initialization workflow: resume from checkpoint if present, search until a passable
    config is found or budget exhausted, activate that config as Demo active seed. No Demo trading
    until this succeeds (when require_viable_seed_before_trading=True, exit non-zero if no viable seed).
    Same underlying logic as run_warm_start_calibration; this is the operator-facing entry point.
    """
    return run_warm_start_calibration(demo_db_path, config_path, artifact_dir)


def _apply_fallback_seed(
    demo_db_path: str,
    config: Config,
    artifact_dir: Path,
    result: dict[str, Any],
) -> None:
    """Register and activate a conservative baseline as Demo active; set result fields."""
    run_stage3_migrations(demo_db_path)
    demo_art = artifact_dir / "configs"
    demo_art.mkdir(parents=True, exist_ok=True)
    fallback_id = register_config_version(
        config,
        version="warm_start_fallback",
        status="candidate",
        description="Conservative fallback (warm-start could not produce viable seed)",
        source="warm_start",
        parent_config_id=None,
        db_path=demo_db_path,
        artifact_dir=demo_art,
    )
    if activate_config_version(fallback_id, demo_db_path, reason="warm_start_fallback", manual=False):
        result["fallback_used"] = True
        result["seed_config_id"] = fallback_id
        result["success"] = True
        result["reason"] = "fallback_seed_activated"
        result["viable_seed_found"] = False
        _register_probation_candidate_if_enabled(config, fallback_id, demo_db_path)
    else:
        result["reason"] = "fallback_activation_failed"
    _write_warm_start_artifact(artifact_dir, result, config)


def _register_probation_candidate_if_enabled(config: Config, seed_config_id: str, demo_db_path: str) -> None:
    """When demo_probation is enabled, record the activated seed as probation candidate (Demo-only)."""
    prob = getattr(config, "demo_probation", None)
    if not prob or not getattr(prob, "enabled", False):
        return
    if not getattr(prob, "allow_demo_trading_with_probation_candidate", True):
        return
    if insert_probation_candidate(seed_config_id, demo_db_path):
        log.info("Demo probation candidate registered: config_id={}", seed_config_id)
        append_demo_lifecycle_event(
            config.artifacts_root, getattr(config, "instance_name", None),
            "PROBATION", "candidate_registered", config_id=seed_config_id,
        )


def _write_warm_start_artifact(artifact_dir: Path, result: dict[str, Any], config: Config) -> None:
    """Write warm_start_report.json to artifacts."""
    import json
    report_dir = Path(artifact_dir) / "warm_start"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "warm_start_report.json"
    payload = {
        "timestamp_ms": int(time.time() * 1000),
        "operating_mode": getattr(config, "operating_mode", None),
        **result,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        log.warning(f"Write warm_start_report: {e}")


def get_warm_start_status(
    demo_db_path: str,
    config_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Return warm-start status for operator visibility: enabled, needed, last seed, fallback."""
    config, env = load_config(config_path)
    mode = get_effective_operating_mode(config, env)
    warm = getattr(config, "warm_start", None)
    from src.warm_start.checkpoint import checkpoint_path
    out: dict[str, Any] = {
        "operating_mode": mode,
        "warm_start_enabled": bool(warm and getattr(warm, "enabled", True)),
        "warm_start_applies": mode == OPERATING_MODE_DEMO_RESEARCH,
        "warm_start_needed": False,
        "reason": "",
        "active_config_id": get_active_config_id(demo_db_path),
        "last_warm_start_report": None,
        "checkpoint_present": False,
    }
    if mode != OPERATING_MODE_DEMO_RESEARCH:
        return out
    needed, reason = is_warm_start_needed(demo_db_path, config)
    out["warm_start_needed"] = needed
    out["reason"] = reason
    report_path = Path(config.artifacts_root) / "warm_start" / "warm_start_report.json"
    if report_path.exists():
        try:
            import json
            with open(report_path, encoding="utf-8") as f:
                out["last_warm_start_report"] = json.load(f)
        except Exception:
            pass
    artifact_dir = Path(config.artifacts_root)
    out["checkpoint_present"] = checkpoint_path(artifact_dir).exists()
    return out
