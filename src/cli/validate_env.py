"""Environment and config validation for install/run prerequisites. Supports dual-key (demo/live) and demo-first burn-in."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _has_dual_key_demo(env: "EnvSettings") -> bool:
    return bool((getattr(env, "bybit_demo_api_key", "") or "").strip() and (getattr(env, "bybit_demo_api_secret", "") or "").strip())


def _has_dual_key_live(env: "EnvSettings") -> bool:
    return bool((getattr(env, "bybit_live_api_key", "") or "").strip() and (getattr(env, "bybit_live_api_secret", "") or "").strip())


def _has_dual_key_testnet(env: "EnvSettings") -> bool:
    return bool((getattr(env, "bybit_testnet_api_key", "") or "").strip() and (getattr(env, "bybit_testnet_api_secret", "") or "").strip())


def _has_legacy_keys(env: "EnvSettings") -> bool:
    return bool((getattr(env, "bybit_api_key", "") or "").strip() and (getattr(env, "bybit_api_secret", "") or "").strip())


def validate_environment(
    config_path: Optional[Path] = None,
    require_api_keys_for_live: bool = True,
) -> ValidationResult:
    """
    Check config exists, .env if needed, dirs writable, mode/env consistency,
    active strategy in registry. Validates dual-key or legacy credentials for selected env (demo/live/testnet).
    """
    errors: list[str] = []
    warnings: list[str] = []

    cfg_path = config_path or Path("config/config.yaml")
    if not cfg_path.exists():
        errors.append(f"Config not found: {cfg_path}. Copy config/config.yaml.example to config/config.yaml")
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    try:
        from src.config.config import load_config, resolve_bybit_credentials, get_bybit_env
        config, env = load_config(cfg_path)
        env_type = get_bybit_env(env)
    except Exception as e:
        errors.append(f"Config load failed: {e}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    env_path = Path(".env")
    if not env_path.exists():
        if config.mode in ("paper", "live") and require_api_keys_for_live:
            errors.append(".env not found. Run: python bootstrap_config.py")
        else:
            warnings.append(".env not found; required for paper/live mode")
    else:
        api_key, api_secret, is_legacy, _ = resolve_bybit_credentials(env, None)
        if config.mode in ("paper", "live"):
            if not api_key or not api_secret:
                errors.append(
                    f"Selected environment is {env_type} but no credentials found. "
                    f"Set BYBIT_DEMO_API_KEY/SECRET for demo or BYBIT_LIVE_API_KEY/SECRET for live (or legacy BYBIT_API_KEY/SECRET)."
                )
            else:
                dual_demo = _has_dual_key_demo(env)
                dual_live = _has_dual_key_live(env)
                if is_legacy:
                    warnings.append("Using legacy BYBIT_API_KEY/SECRET. Recommend dual-key: BYBIT_DEMO_API_KEY/SECRET and BYBIT_LIVE_API_KEY/SECRET.")
                # Mismatches
                if env_type == "demo" and not dual_demo and dual_live and not _has_legacy_keys(env):
                    errors.append("BYBIT_ENV=demo but only BYBIT_LIVE_API_KEY/SECRET are set. Set BYBIT_DEMO_API_KEY/SECRET for demo burn-in.")
                if env_type == "live" and not dual_live and dual_demo and not _has_legacy_keys(env):
                    errors.append("BYBIT_ENV=live but only BYBIT_DEMO_API_KEY/SECRET are set. Set BYBIT_LIVE_API_KEY/SECRET for live.")
                if env_type == "testnet":
                    warnings.append("BYBIT_ENV=testnet is legacy. Recommend BYBIT_ENV=demo for burn-in (Bybit Demo Trading).")

    # DB and artifact dirs
    db_path = Path(config.database_path)
    db_dir = db_path.parent
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        errors.append(f"DB directory not writable: {db_dir} - {e}")
    else:
        try:
            (db_dir / ".write_check").write_text("")
            (db_dir / ".write_check").unlink()
        except OSError as e:
            errors.append(f"DB directory not writable: {db_dir} - {e}")

    for name in ("artifacts", "artifacts/burnin", "artifacts/validation", "logs"):
        p = Path(name)
        try:
            p.mkdir(parents=True, exist_ok=True)
            (p / ".write_check").write_text("")
            (p / ".write_check").unlink()
        except OSError as e:
            errors.append(f"Directory not writable: {name} - {e}")

    # Mode / env consistency
    testnet_cfg = getattr(config.exchange, "testnet", True)
    if env_type == "testnet" and not testnet_cfg:
        warnings.append("BYBIT_ENV=testnet but config exchange.testnet is false (config overridden by env)")
    if env_type in ("demo", "live") and testnet_cfg:
        warnings.append("exchange.testnet is true but env is %s (config overridden by env)" % env_type)

    if config.mode == "live" and env_type == "demo":
        warnings.append("mode is 'live' but BYBIT_ENV=demo. For guarded live set BYBIT_ENV=live and live keys.")

    burn_in = getattr(config, "burn_in", None)
    if burn_in and getattr(burn_in, "burn_in_enabled", False):
        if getattr(config, "dry_run", False) is True and getattr(burn_in, "burn_in_phase", "") == "demo":
            warnings.append("dry_run is true: Demo burn-in will simulate only (no real orders). Set dry_run: false in config to place real Demo orders.")
        phase = getattr(burn_in, "burn_in_phase", "demo")
        if config.mode == "live" and phase in ("demo", "testnet"):
            warnings.append("Burn-in phase is '%s' but mode is 'live'. Set burn_in_phase to live_small for guarded live." % phase)
        if phase == "live_small" and env_type != "live":
            warnings.append("Burn-in phase is 'live_small' but BYBIT_ENV is %s. Set BYBIT_ENV=live for small live." % env_type)

    # Active strategy in registry
    try:
        from src.strategies.registry import get_strategy, list_strategies
        active = getattr(config, "active_strategy", "flow_impulse")
        names = list_strategies()
        if active not in names:
            errors.append(f"Active strategy '{active}' not in registry: {names}")
        elif get_strategy(active, config) is None:
            errors.append(f"Strategy '{active}' failed to load")
    except Exception as e:
        errors.append(f"Strategy registry check: {e}")

    return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)
