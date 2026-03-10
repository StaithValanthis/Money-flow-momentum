"""Environment and config validation for install/run prerequisites."""

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


def validate_environment(
    config_path: Optional[Path] = None,
    require_api_keys_for_live: bool = True,
) -> ValidationResult:
    """
    Check config exists, .env if needed, dirs writable, mode/testnet consistency,
    active strategy in registry. Fail clearly when prerequisites are missing.
    """
    errors: list[str] = []
    warnings: list[str] = []

    cfg_path = config_path or Path("config/config.yaml")
    if not cfg_path.exists():
        errors.append(f"Config not found: {cfg_path}. Copy config/config.yaml.example to config/config.yaml")
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    try:
        from src.config.config import load_config
        config, env = load_config(cfg_path)
    except Exception as e:
        errors.append(f"Config load failed: {e}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    # .env for paper/live when we need API
    env_path = Path(".env")
    if not env_path.exists():
        if config.mode in ("paper", "live") and require_api_keys_for_live:
            errors.append(".env not found. Run: python bootstrap_config.py")
        else:
            warnings.append(".env not found; required for paper/live mode")
    else:
        if config.mode in ("paper", "live") and (not env.bybit_api_key or not env.bybit_api_secret):
            errors.append(".env exists but BYBIT_API_KEY or BYBIT_API_SECRET missing")

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

    # Mode / testnet consistency
    testnet_env = getattr(env, "bybit_testnet", True)
    testnet_cfg = getattr(config.exchange, "testnet", True)
    if testnet_env != testnet_cfg:
        warnings.append(f"BYBIT_TESTNET (.env) and exchange.testnet (config) mismatch: {testnet_env} vs {testnet_cfg}")

    if config.mode == "live" and testnet_cfg:
        warnings.append("mode is 'live' but exchange.testnet is true - confirm intended testnet usage")

    burn_in = getattr(config, "burn_in", None)
    if burn_in and getattr(burn_in, "burn_in_enabled", False):
        phase = getattr(burn_in, "burn_in_phase", "testnet")
        if config.mode == "live" and phase == "testnet":
            warnings.append("Burn-in phase is 'testnet' but mode is 'live'. Set burn_in_phase to live_small for guarded live.")
        if config.mode in ("paper", "live") and phase == "live_small" and testnet_cfg:
            warnings.append("Burn-in phase is 'live_small' but exchange.testnet is true. Use mainnet for small live.")

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
