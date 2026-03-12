"""Candidate config generation from parent/baseline; only approved fields mutated."""

from pathlib import Path
from typing import Any, Optional

from src.config.config import Config
from src.config.versioning import (
    register_config_version,
    load_config_from_artifact,
    compute_config_hash,
)
from src.utils.logging import get_logger

log = get_logger(__name__)

# Keys that are allowed to be modified by optimizer/candidate generation (nested under top-level)
APPROVED_PARAM_PATHS = frozenset({
    "entry.long_threshold",
    "entry.short_threshold",
    "entry.min_delta_1m",
    "entry.min_buy_sell_ratio_long",
    "entry.max_buy_sell_ratio_short",
    "entry.use_adaptive_thresholds",
    "entry.use_regime_filter",
    "entry.regime_block_trend",
    "entry.regime_block_chop",
    "entry.anti_chase_penalty",
    "entry.persistence_bonus",
    "entry.max_positions_per_cluster",
    "score_weights.w1_delta_1m",
    "score_weights.w2_cvd_slope_3m",
    "score_weights.w3_buy_sell_ratio_1m",
    "score_weights.w4_price_return_1m",
    "score_weights.w5_oi_change",
    "score_weights.w6_spread_penalty",
    "score_weights.w7_funding_penalty",
    "stop_tp.atr_multiplier_sl",
    "stop_tp.tp1_r_multiple",
    "stop_tp.tp2_r_multiple",
    "stop_tp.tp1_pct",
    "stop_tp.tp2_pct",
    "stop_tp.trailing_stop_atr_multiple",
    "stop_tp.time_stop_bars",
    "stop_tp.exhaustion_exit_enabled",
    "stop_tp.exhaustion_flow_price_ratio_max",
    "stop_tp.failed_breakout_exit_enabled",
    "stop_tp.failed_breakout_reversal_pct",
    "stop_tp.volatility_aware_time_stop",
    "stop_tp.time_stop_vol_multiplier",
    "risk.risk_per_trade_pct",
    "risk.reentry_cooldown_seconds",
    "risk.symbol_cooldown_after_stop_seconds",
    "risk.allocation_method",
    "risk.max_cluster_risk_pct",
    "risk.max_long_risk_pct",
    "risk.max_short_risk_pct",
    "portfolio_exposure.max_gross_exposure_per_cluster_pct",
    "portfolio_exposure.max_risk_per_cluster_pct",
    "portfolio_exposure.same_direction_concentration_penalty_pct",
})


def _set_nested(d: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _get_nested(d: dict, path: str) -> Any:
    for p in path.split("."):
        d = d.get(p, {})
    return d


def build_config_from_params(parent_config: Config, param_overrides: dict[str, Any]) -> Optional[Config]:
    """
    Build a Config from parent with only approved keys in param_overrides.
    Does not register in DB. Returns None if invalid.
    """
    from copy import deepcopy
    invalid = set(param_overrides.keys()) - APPROVED_PARAM_PATHS
    overrides = {k: v for k, v in param_overrides.items() if k in APPROVED_PARAM_PATHS}
    if invalid:
        log.debug(f"build_config_from_params ignoring non-approved: {invalid}")
    d = parent_config.model_dump(mode="json")
    for path, value in overrides.items():
        if path in APPROVED_PARAM_PATHS:
            _set_nested(d, path, value)
    try:
        return Config.model_validate(d)
    except Exception as e:
        log.debug(f"build_config_from_params validation: {e}")
        return None


def generate_candidate(
    parent_config: Config,
    param_overrides: dict[str, Any],
    version: str = "",
    description: str = "",
    source: str = "optimizer",
    optimizer_run_id: Optional[str] = None,
    windows_json: str = "",
    objective_summary: str = "",
    reason_codes: str = "",
    expected_improvements: str = "",
    caveats: str = "",
    db_path: str = "data/bot.db",
    artifact_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Create a new config from parent with only approved keys in param_overrides.
    param_overrides: flat keys like entry.long_threshold -> value.
    Returns config_id or None.
    """
    invalid = set(param_overrides.keys()) - APPROVED_PARAM_PATHS
    if invalid:
        log.warning(f"Ignoring non-approved param paths: {invalid}")
        for k in invalid:
            del param_overrides[k]

    d = parent_config.model_dump(mode="json")
    for path, value in param_overrides.items():
        if path in APPROVED_PARAM_PATHS:
            _set_nested(d, path, value)

    try:
        candidate = Config.model_validate(d)
    except Exception as e:
        log.error(f"Invalid candidate config: {e}")
        return None

    if not version:
        version = f"cand_{compute_config_hash(candidate)[:8]}"

    parent_id = None
    from src.config.versioning import list_config_versions
    parent_hash = compute_config_hash(parent_config)
    for rec in list_config_versions(limit=500, db_path=db_path):
        if rec.get("config_hash") == parent_hash:
            parent_id = rec.get("config_id")
            break

    config_id = register_config_version(
        candidate,
        version=version,
        status="candidate",
        description=description or "Generated candidate",
        source=source,
        parent_config_id=parent_id,
        db_path=db_path,
        artifact_dir=artifact_dir,
    )

    from src.storage.db import Database
    from src.storage.migrations import run_stage3_migrations
    run_stage3_migrations(db_path)
    db = Database(db_path)
    conn = db._get_conn()
    conn.execute(
        """INSERT INTO candidate_configs
           (config_id, optimizer_run_id, parent_config_id, windows_json, objective_summary, reason_codes, expected_improvements, caveats, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            config_id,
            optimizer_run_id or "",
            parent_id,
            windows_json,
            objective_summary,
            reason_codes,
            expected_improvements,
            caveats,
            int(__import__("time").time() * 1000),
        ),
    )
    conn.commit()
    db.close()

    return config_id
