"""Warm-start checkpoint/resume: persist iterative search state so runs can continue after stop/rerun."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.config import Config
from src.utils.logging import get_logger

log = get_logger(__name__)

CHECKPOINT_FILENAME = "warm_start_checkpoint.json"
ARCHIVE_PREFIX = "warm_start_checkpoint_archive_"


def get_warm_start_fingerprint(
    config: Config,
    symbols_sorted: Optional[List[str]] = None,
    timeframe: str = "",
    lookback_days: int = 0,
) -> str:
    """Public helper for tests: return the same fingerprint used for checkpoint matching."""
    return _warm_start_fingerprint(config, symbols_sorted, timeframe, lookback_days)


def _warm_start_fingerprint(config: Config, symbols_sorted: Optional[List[str]] = None, timeframe: str = "", lookback_days: int = 0) -> str:
    """Stable hash of warm_start settings and search context so we only resume when config matches."""
    warm = getattr(config, "warm_start", None)
    if not warm:
        return ""
    # Include fields that affect search or acceptance
    data: Dict[str, Any] = {}
    for k in (
        "enabled", "search_until_viable", "batch_n_samples", "max_batches", "max_total_runtime_seconds",
        "require_profitable_seed", "require_viable_seed_before_trading", "allow_fallback_if_no_viable_seed",
        "prioritize_protection_search", "protection_search_bias",
        "backtest_fee_bps", "backtest_slippage_bps",
        "min_replay_trade_count", "min_win_rate", "min_profit_factor", "min_payoff_ratio",
        "max_replay_drawdown", "min_median_trade_duration_sec", "ultra_short_duration_sec",
        "max_ultra_short_trade_fraction", "max_stop_out_rate", "max_consecutive_losses", "min_tp1_hit_rate",
        "use_multi_window_validation", "validation_split_count", "use_cost_sensitivity_check",
        "cost_scenarios_bps", "use_regime_validation", "regime_quarters_min_positive",
        "use_overfitting_diagnostics", "reject_on_high_overfitting_risk", "max_acceptable_overfitting_risk",
        "reject_on_research_validation_failure", "min_validation_fold_positive_fraction",
        "min_cost_scenarios_profitable",
    ):
        if hasattr(warm, k):
            data[k] = getattr(warm, k)
    data["timeframe"] = timeframe
    data["lookback_days"] = lookback_days
    data["symbols_sorted"] = symbols_sorted if symbols_sorted is not None else []
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def checkpoint_path(artifact_dir: Path) -> Path:
    """Path to the warm_start checkpoint file (under artifact_dir/warm_start/)."""
    return Path(artifact_dir) / "warm_start" / CHECKPOINT_FILENAME


def load_checkpoint(artifact_dir: Path) -> Optional[Dict[str, Any]]:
    """Load checkpoint from disk if present. Returns None if missing or invalid."""
    path = checkpoint_path(artifact_dir)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        log.warning("Warm-start checkpoint load failed: {}", e)
        return None


def save_checkpoint(artifact_dir: Path, payload: Dict[str, Any]) -> None:
    """Write checkpoint to disk."""
    path = checkpoint_path(artifact_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        log.warning("Warm-start checkpoint save failed: {}", e)


def checkpoint_matches(
    config: Config,
    checkpoint: Dict[str, Any],
    symbols_sorted: Optional[List[str]] = None,
    timeframe: str = "",
    lookback_days: int = 0,
) -> bool:
    """True if current config/symbols/timeframe/lookback match the checkpoint fingerprint."""
    current = _warm_start_fingerprint(config, symbols_sorted, timeframe, lookback_days)
    stored = (checkpoint or {}).get("warm_start_settings_fingerprint") or ""
    return bool(current and stored and current == stored)


def clear_checkpoint(artifact_dir: Path) -> None:
    """Remove checkpoint file (e.g. after success or exhaustion)."""
    path = checkpoint_path(artifact_dir)
    if path.exists():
        try:
            path.unlink()
            log.info("Warm-start checkpoint cleared")
        except Exception as e:
            log.warning("Warm-start checkpoint clear failed: {}", e)


def archive_checkpoint(artifact_dir: Path) -> None:
    """Move checkpoint to timestamped archive so run is finalised but history kept."""
    path = checkpoint_path(artifact_dir)
    if not path.exists():
        return
    try:
        ts = int(time.time())
        archive_name = f"{ARCHIVE_PREFIX}{ts}.json"
        archive_path = path.parent / archive_name
        path.rename(archive_path)
        log.info("Warm-start checkpoint archived to {}", archive_path.name)
    except Exception as e:
        log.warning("Warm-start checkpoint archive failed: {}", e)


def build_checkpoint_payload(
    result: Dict[str, Any],
    config: Config,
    symbols_sorted: List[str],
    timeframe: str,
    lookback_days: int,
    engine: str = "parameter_aware_protection_backtest",
) -> Dict[str, Any]:
    """Build dict to persist as checkpoint (iterative search state only)."""
    fp = _warm_start_fingerprint(config, symbols_sorted, timeframe, lookback_days)
    return {
        "timestamp_ms": int(time.time() * 1000),
        "search_until_viable": result.get("search_until_viable", True),
        "batches_completed": int(result.get("batches_completed") or 0),
        "max_batches": result.get("max_batches"),
        "max_total_runtime_seconds": result.get("max_total_runtime_seconds"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "total_candidates_requested": int(result.get("total_candidates_requested") or 0),
        "total_candidates_evaluated": int(result.get("total_candidates_evaluated") or 0),
        "total_candidates_replayed": int(result.get("total_candidates_replayed") or 0),
        "best_candidate_config_id_so_far": result.get("best_candidate_config_id"),
        "best_candidate_metrics_so_far": result.get("best_candidate_metrics"),
        "best_candidate_params_so_far": result.get("best_candidate_params_so_far"),
        "best_rejection_reason_seen": result.get("best_rejection_reason_seen"),
        "viable_seed_found": bool(result.get("viable_seed_found", False)),
        "last_completed_batch_index": int(result.get("batches_completed") or 0) - 1,  # 0-based
        "total_candidates_invalid": int(result.get("candidates_invalid") or 0),
        "warm_start_settings_fingerprint": fp,
        "config_fingerprint": fp,
        "engine": engine,
    }
