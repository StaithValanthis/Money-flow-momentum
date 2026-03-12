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

    log.info("Warm-start started (Demo-only)")
    artifact_dir = artifact_dir or Path(config.artifacts_root)
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
        log.info("Warm-start universe resolved: %d symbols", len(symbols))
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
            log.info("Warm-start candles fetched: %d symbols", len(candles_by_symbol))
    if candles_by_symbol:
        log.info("Warm-start candles ready: %d symbols", len(candles_by_symbol))

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

    log.info("Warm-start candidate search started (n_samples=%s, max_runtime_seconds=%s)", n_samples, max_runtime_seconds)
    # --- Primary path: parameter-aware replay (evaluate each candidate via replay on candles) ---
    try:
        best, all_results, search_meta = run_warm_start_candidate_search(
            baseline_config,
            candles_by_symbol,
            n_samples=n_samples,
            min_trades_guardrail=5,
            require_profitable=require_profitable,
            max_runtime_seconds=max_runtime_seconds,
            start_time=calibration_start,
        )
        result["candidate_count_requested"] = search_meta.get("candidate_count_requested", n_samples)
        result["candidates_invalid"] = search_meta.get("candidates_invalid")
        result["candidates_replayed"] = search_meta.get("candidates_replayed")
        result["candidate_count_evaluated"] = search_meta.get("candidates_replayed")
        result["no_trades_reason"] = search_meta.get("no_trades_reason")
        result["timeout_hit"] = search_meta.get("timeout_hit", False)
        result["elapsed_seconds"] = search_meta.get("elapsed_seconds")
        if result["elapsed_seconds"] is None:
            result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
        if best is not None:
            log.info("Warm-start best candidate selected (score from replay); activating seed")
            winning_config = build_config_from_params(baseline_config, best["params"])
            if winning_config:
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
                    result["engine"] = "parameter_aware_replay"
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
                    if result.get("timeout_hit"):
                        result["reason"] = "warm_start_seeded_timeout_best_so_far"
                    log.info("Warm-start final seed activated: config_id=%s", new_id)
                    _write_warm_start_artifact(artifact_dir, result, config)
                    return result
    except Exception as e:
        log.warning("Parameter-aware warm-start failed, falling back to single-replay path: %s", e)
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

        if best_id and require_profitable:
            data = load_evaluation_dataset(temp_db, from_ts=from_ts_ms, to_ts=to_ts_ms, config_id=best_id)
            trades = compute_realized_pnl_by_pairing(data["trades"])
            metrics = compute_core_metrics(trades)
            if float(metrics.get("total_pnl") or 0) <= 0:
                best_id = None
                result["reason"] = "no_profitable_seed"

        if best_id:
            # Copy best config from temp DB to Demo DB and activate
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
                _write_warm_start_artifact(artifact_dir, result, config)
                return result
            result["reason"] = "activation_failed"
        else:
            result["reason"] = result.get("reason") or "no_acceptable_candidate"

    result["elapsed_seconds"] = round(time.time() - calibration_start, 2)
    if fallback:
        log.info("Warm-start fallback: no acceptable candidate; activating conservative seed (reason=%s)", result.get("reason"))
        _apply_fallback_seed(demo_db_path, config, artifact_dir, result)
    return result


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
    else:
        result["reason"] = "fallback_activation_failed"
    _write_warm_start_artifact(artifact_dir, result, config)


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
    out: dict[str, Any] = {
        "operating_mode": mode,
        "warm_start_enabled": bool(warm and getattr(warm, "enabled", True)),
        "warm_start_applies": mode == OPERATING_MODE_DEMO_RESEARCH,
        "warm_start_needed": False,
        "reason": "",
        "active_config_id": get_active_config_id(demo_db_path),
        "last_warm_start_report": None,
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
    return out
