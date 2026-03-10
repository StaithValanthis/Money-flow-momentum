"""Safe promote-environment helper: Demo -> guarded Live with prechecks and explicit confirmation."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from src.utils.logging import get_logger
from src.config.config import load_config, get_bybit_env, resolve_bybit_credentials
from src.validation.readiness import (
    compute_readiness,
    ReadinessResult,
    READINESS_READY_SMALL_LIVE,
    READINESS_NOT_READY,
    READINESS_NEEDS_REVIEW,
)
from src.config.versioning import get_active_config_id
from src.storage.artifacts import validation_dir, ensure_artifact_dirs

log = get_logger(__name__)

ACCEPTED_READINESS = (READINESS_READY_SMALL_LIVE,)


@dataclass
class PromoteEnvPrecheckResult:
    ok: bool
    current_env: str
    live_credentials_present: bool
    live_credentials_legacy: bool
    readiness: Optional[ReadinessResult] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_promote_env_prechecks(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    window_hours: float = 24.0,
) -> PromoteEnvPrecheckResult:
    """
    Run all prechecks for promoting Demo -> Live.
    Returns result with ok=True only when: env is demo, live keys present, readiness is READY_FOR_SMALL_LIVE.
    """
    config_path = config_path or Path("config/config.yaml")
    env_path = env_path or Path(".env")
    errors: list[str] = []
    warnings: list[str] = []

    if not config_path.exists():
        return PromoteEnvPrecheckResult(
            ok=False,
            current_env="",
            live_credentials_present=False,
            live_credentials_legacy=False,
            errors=["Config file not found: %s" % config_path],
        )

    try:
        config, env = load_config(config_path)
    except Exception as e:
        return PromoteEnvPrecheckResult(
            ok=False,
            current_env="",
            live_credentials_present=False,
            live_credentials_legacy=False,
            errors=["Config load failed: %s" % e],
        )

    current_env = get_bybit_env(env)
    if current_env != "demo":
        errors.append(
            "Current environment is '%s'. Promote-environment only switches from Demo to Live. Set BYBIT_ENV=demo and run demo burn-in first."
            % current_env
        )

    # Live credentials
    live_key, live_secret, is_legacy, _ = resolve_bybit_credentials(env, "live")
    live_credentials_present = bool(live_key and live_secret)
    live_credentials_legacy = is_legacy
    if not live_credentials_present:
        errors.append(
            "Live credentials missing. Set BYBIT_LIVE_API_KEY and BYBIT_LIVE_API_SECRET (or legacy BYBIT_API_KEY/SECRET) in .env"
        )
    if live_credentials_legacy and live_credentials_present:
        warnings.append("Using legacy single-key for live. Recommend dual-key: BYBIT_DEMO_API_KEY/SECRET and BYBIT_LIVE_API_KEY/SECRET.")

    # Burn-in config
    burn_in = getattr(config, "burn_in", None)
    if not burn_in or not getattr(burn_in, "burn_in_enabled", False):
        errors.append("Burn-in is not enabled. Set burn_in.burn_in_enabled: true in config.")
    phase = getattr(burn_in, "burn_in_phase", "demo") if burn_in else "demo"
    if phase not in ("demo", "testnet"):
        errors.append(
            "Current burn_in_phase is '%s'. Promote-environment expects phase demo (or testnet). Switch only from demo/testnet to live_small."
            % phase
        )

    # Readiness: evaluate as if we're in live_small (target state)
    readiness: Optional[ReadinessResult] = None
    if not env_path.exists():
        errors.append(".env not found at %s" % env_path)
    else:
        try:
            from src.storage.db import Database
            db = Database(config.database_path)
            hb_path = Path("artifacts/heartbeat.json")
            config_id = get_active_config_id(config.database_path)
            readiness = compute_readiness(
                db,
                heartbeat_path=hb_path,
                config_id=config_id,
                window_hours=window_hours,
                burn_in_phase="live_small",
            )
            db.close()
        except Exception as e:
            log.debug("Readiness computation failed: %s", e)
            errors.append("Readiness check failed: %s" % e)

    if readiness and readiness.classification not in ACCEPTED_READINESS:
        errors.append(
            "Readiness is '%s'. Only %s is accepted for promotion. Resolve issues (gate breaches, protection mismatch, execution drift, etc.) and re-run burnin readiness."
            % (readiness.classification, READINESS_READY_SMALL_LIVE)
        )
    elif readiness and readiness.details.get("trade_count", 0) == 0 and not readiness.details.get("heartbeat_coverage"):
        warnings.append("No burn-in activity in window (no trades, no heartbeat). Ensure demo burn-in was run before promoting.")

    ok = len(errors) == 0
    return PromoteEnvPrecheckResult(
        ok=ok,
        current_env=current_env,
        live_credentials_present=live_credentials_present,
        live_credentials_legacy=live_credentials_legacy,
        readiness=readiness,
        errors=errors,
        warnings=warnings,
    )


def _backup_file(path: Path) -> Optional[Path]:
    """Create a timestamped backup; return backup path or None."""
    if not path.exists():
        return None
    ts = int(time.time() * 1000)
    backup = path.parent / ("%s.bak.%s" % (path.name, ts))
    try:
        backup.write_bytes(path.read_bytes())
        return backup
    except OSError as e:
        log.warning("Backup failed for %s: %s", path, e)
        return None


def _update_env_file_to_live(env_path: Path) -> tuple[bool, str]:
    """Set BYBIT_ENV=live in .env; preserve other lines. Returns (success, message)."""
    if not env_path.exists():
        return False, ".env does not exist"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
        out = []
        found = False
        for line in lines:
            if line.strip().startswith("BYBIT_ENV="):
                out.append("BYBIT_ENV=live")
                found = True
            else:
                out.append(line)
        if not found:
            out.append("BYBIT_ENV=live")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True, "Set BYBIT_ENV=live"
    except OSError as e:
        return False, str(e)


def _update_config_burn_in_phase(config_path: Path, new_phase: str = "live_small") -> tuple[bool, str]:
    """Set burn_in.burn_in_phase to new_phase in config YAML. Returns (success, message)."""
    if not config_path.exists():
        return False, "Config file does not exist"
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        data.setdefault("burn_in", {})
        data["burn_in"]["burn_in_phase"] = new_phase
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True, "Set burn_in.burn_in_phase=%s" % new_phase
    except Exception as e:
        return False, str(e)


def apply_promote_env(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    backup: bool = True,
    reason: Optional[str] = None,
) -> tuple[bool, dict]:
    """
    Apply environment promotion: backup .env and config, set BYBIT_ENV=live and burn_in_phase=live_small.
    Caller must have run prechecks and confirmed. Returns (success, report_dict).
    """
    config_path = config_path or Path("config/config.yaml")
    env_path = env_path or Path(".env")
    report: dict = {
        "timestamp_ms": int(time.time() * 1000),
        "previous_environment": "demo",
        "new_environment": "live",
        "previous_burn_in_phase": "demo",
        "new_burn_in_phase": "live_small",
        "files_changed": [],
        "backups_created": [],
        "reason": reason or "",
        "confirmation_used": True,
        "start_live_requested": False,
    }

    try:
        config, env = load_config(config_path)
        report["active_config_id"] = get_active_config_id(config.database_path)
        report["active_strategy"] = getattr(config, "active_strategy", "flow_impulse")
        report["previous_burn_in_phase"] = getattr(getattr(config, "burn_in", None), "burn_in_phase", "demo")
    except Exception as e:
        report["error"] = str(e)
        return False, report

    backups_created = []
    if backup:
        b1 = _backup_file(env_path)
        if b1:
            backups_created.append(str(b1))
        b2 = _backup_file(config_path)
        if b2:
            backups_created.append(str(b2))
    report["backups_created"] = backups_created

    ok_env, msg_env = _update_env_file_to_live(env_path)
    if ok_env:
        report["files_changed"].append(str(env_path))
    else:
        report["error"] = "Failed to update .env: %s" % msg_env
        return False, report

    ok_cfg, msg_cfg = _update_config_burn_in_phase(config_path, "live_small")
    if ok_cfg:
        report["files_changed"].append(str(config_path))
    else:
        report["error"] = "Failed to update config: %s" % msg_cfg
        return False, report

    return True, report


def write_promotion_artifact(report: dict, base_dir: Optional[Path] = None) -> Path:
    """Write promotion report to artifacts/validation/env_promotion_<ts>.json and .md. Returns path to JSON."""
    ensure_artifact_dirs(base_dir)
    ts = report.get("timestamp_ms", int(time.time() * 1000))
    val_dir = validation_dir(base_dir)
    val_dir.mkdir(parents=True, exist_ok=True)
    path_json = val_dir / ("env_promotion_%s.json" % ts)
    path_md = val_dir / ("env_promotion_%s.md" % ts)
    import json
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    lines = [
        "# Environment promotion",
        "",
        "- **Time:** %s" % ts,
        "- **Previous environment:** %s" % report.get("previous_environment", ""),
        "- **New environment:** %s" % report.get("new_environment", ""),
        "- **Previous phase:** %s" % report.get("previous_burn_in_phase", ""),
        "- **New phase:** %s" % report.get("new_burn_in_phase", ""),
        "- **Files changed:** %s" % ", ".join(report.get("files_changed", [])),
        "- **Backups:** %s" % ", ".join(report.get("backups_created", [])),
        "- **Reason:** %s" % report.get("reason", ""),
        "",
    ]
    if report.get("error"):
        lines.append("**Error:** %s" % report["error"])
    path_md.write_text("\n".join(lines), encoding="utf-8")
    return path_json
