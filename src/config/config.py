"""Configuration loading and validation using Pydantic."""

from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from src.utils.logging import get_logger

load_dotenv()

logger = get_logger(__name__)


"""Configuration loading and validation using Pydantic."""

from pathlib import Path
from typing import Optional, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from src.utils.logging import get_logger

load_dotenv()

logger = get_logger(__name__)

BybitEnvType = Literal["demo", "live", "testnet"]


class EnvSettings(BaseSettings):
    """Secrets and env-only settings. Prefer dual-key (demo + live) for Bybit Demo Trading and mainnet."""

    # Environment selector: demo (recommended burn-in) | live | testnet (legacy)
    bybit_env: str = "demo"
    # Legacy single pair (backward compatibility)
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    # Legacy: true = testnet, false = live (ignored if bybit_env is set)
    bybit_testnet: bool = True
    # Dual-key: demo (Bybit Demo Trading; keys from mainnet account Demo mode)
    bybit_demo_api_key: str = ""
    bybit_demo_api_secret: str = ""
    # Dual-key: live/mainnet
    bybit_live_api_key: str = ""
    bybit_live_api_secret: str = ""
    # Legacy testnet keys (secondary; do not use with demo)
    bybit_testnet_api_key: str = ""
    bybit_testnet_api_secret: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def get_bybit_env(env: EnvSettings) -> BybitEnvType:
    """Resolve effective environment: BYBIT_ENV if set, else BYBIT_TESTNET -> testnet/live."""
    e = (getattr(env, "bybit_env", "") or "").strip().lower()
    if e in ("demo", "live", "testnet"):
        return e  # type: ignore
    if getattr(env, "bybit_testnet", True):
        return "testnet"
    return "live"


def resolve_bybit_credentials(env: EnvSettings, env_type: Optional[BybitEnvType] = None) -> tuple[str, str, bool, BybitEnvType]:
    """
    Resolve effective Bybit API key and secret for the given environment.
    env_type: demo | live | testnet. If None, uses get_bybit_env(env).
    Returns (api_key, api_secret, is_legacy_fallback, effective_env_type).
    """
    effective = env_type or get_bybit_env(env)
    if effective == "demo":
        key = (env.bybit_demo_api_key or "").strip()
        secret = (env.bybit_demo_api_secret or "").strip()
        if key and secret:
            return key, secret, False, "demo"
        key = (env.bybit_api_key or "").strip()
        secret = (env.bybit_api_secret or "").strip()
        if key and secret:
            return key, secret, True, "demo"
        return "", "", False, "demo"
    if effective == "live":
        key = (env.bybit_live_api_key or "").strip()
        secret = (env.bybit_live_api_secret or "").strip()
        if key and secret:
            return key, secret, False, "live"
        key = (env.bybit_api_key or "").strip()
        secret = (env.bybit_api_secret or "").strip()
        if key and secret:
            return key, secret, True, "live"
        return "", "", False, "live"
    # testnet (legacy)
    key = (env.bybit_testnet_api_key or "").strip()
    secret = (env.bybit_testnet_api_secret or "").strip()
    if key and secret:
        return key, secret, False, "testnet"
    key = (env.bybit_api_key or "").strip()
    secret = (env.bybit_api_secret or "").strip()
    if key and secret:
        return key, secret, True, "testnet"
    return "", "", False, "testnet"


class ExchangeConfig(BaseModel):
    """Exchange connection settings."""

    testnet: bool = True
    one_way_mode: bool = True
    default_leverage: int = Field(default=5, ge=1, le=100)
    request_timeout: float = 10.0
    ws_ping_interval: float = 20.0
    ws_reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 10
    rate_limit_requests_per_second: float = 5.0


class UniverseConfig(BaseModel):
    """Universe discovery and filtering."""

    min_24h_turnover_usdt: float = 1_000_000.0
    max_spread_bps: float = 50.0
    min_notional_usdt: float = 100.0
    allowlist: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    status_filter: str = "Trading"
    quote_coin: str = "USDT"


class FeatureConfig(BaseModel):
    """Feature window sizes in seconds."""

    window_30s: int = 30
    window_1m: int = 60
    window_3m: int = 180
    window_5m: int = 300
    atr_period: int = 14
    volatility_window: int = 60


class ScoreWeights(BaseModel):
    """Weights for cross-sectional scoring."""

    w1_delta_1m: float = 0.25
    w2_cvd_slope_3m: float = 0.20
    w3_buy_sell_ratio_1m: float = 0.20
    w4_price_return_1m: float = 0.15
    w5_oi_change: float = 0.10
    w6_spread_penalty: float = 0.05
    w7_funding_penalty: float = 0.05


class EntryThresholds(BaseModel):
    """Entry condition thresholds."""

    long_threshold: float = 1.5
    short_threshold: float = -1.5
    min_delta_1m: float = 0.0
    min_buy_sell_ratio_long: float = 1.05
    max_buy_sell_ratio_short: float = 0.95
    max_spread_bps: float = 30.0
    max_atr_extension: float = 2.0
    divergence_bonus: float = 0.3
    max_correlation_positions: int = 5
    # Stage 4: adaptive / regime
    use_adaptive_thresholds: bool = False
    use_regime_filter: bool = False
    regime_block_trend: bool = False
    regime_block_chop: bool = False
    anti_chase_penalty: float = 0.0
    persistence_bonus: float = 0.0
    max_positions_per_cluster: int = 2


class StopTPConfig(BaseModel):
    """Stop loss and take profit settings."""

    atr_multiplier_sl: float = 1.5
    tp1_r_multiple: float = 1.0
    tp2_r_multiple: float = 2.0
    tp1_pct: float = 0.33
    tp2_pct: float = 0.33
    breakeven_after_tp1: bool = True
    max_hold_seconds: int = 3600
    flow_reversal_exit: bool = True
    flow_reversal_delta_threshold: float = -0.5
    trailing_stop_atr_multiple: float = Field(default=1.0, ge=0.5)
    time_stop_bars: int = Field(default=60, ge=0)
    # Stage 4 exit refinements
    exhaustion_exit_enabled: bool = False
    exhaustion_flow_price_ratio_max: float = 2.0
    failed_breakout_exit_enabled: bool = False
    failed_breakout_reversal_pct: float = 0.003
    volatility_aware_time_stop: bool = False
    time_stop_vol_multiplier: float = 1.0


class RiskConfig(BaseModel):
    """Risk management settings."""

    risk_per_trade_pct: float = Field(default=0.5, ge=0.1, le=2.0)
    max_concurrent_positions: int = Field(default=5, ge=1, le=20)
    max_positions_per_side: int = Field(default=3, ge=1, le=10)
    max_total_risk_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    max_daily_drawdown_pct: float = Field(default=5.0, ge=1.0, le=20.0)
    max_daily_realized_loss_usdt: float = Field(default=500.0, ge=0)
    max_notional_per_symbol_usdt: float = 10_000.0
    max_portfolio_notional_usdt: float = Field(default=50_000.0, ge=0)
    min_notional_per_trade_usdt: float = 10.0
    cooldown_after_loss_seconds: int = 300
    reentry_cooldown_seconds: int = Field(default=60, ge=0)
    kill_switch_enabled: bool = True
    stale_data_seconds: float = 60.0
    max_trades_per_hour: int = Field(default=30, ge=1, le=200)
    api_error_threshold: int = Field(default=10, ge=1)
    symbol_cooldown_after_stop_seconds: int = Field(default=600, ge=0)
    # Stage 5 portfolio risk budgeting
    max_long_risk_pct: float = Field(default=0, ge=0, le=10.0)
    max_short_risk_pct: float = Field(default=0, ge=0, le=10.0)
    max_cluster_risk_pct: float = Field(default=0, ge=0, le=10.0)
    allocation_method: str = Field(default="equal_risk", pattern="^(equal_risk|score_weighted|capped_score_weighted|cluster_aware)$")


class PortfolioExposureConfig(BaseModel):
    """Stage 5: exposure and correlation controls."""

    max_gross_exposure_per_cluster_pct: float = Field(default=0, ge=0, le=100)
    max_risk_per_cluster_pct: float = Field(default=0, ge=0, le=20)
    max_correlated_positions: int = Field(default=0, ge=0, le=20)
    same_direction_concentration_penalty_pct: float = Field(default=0, ge=0, le=50)


class ExecutionConfig(BaseModel):
    """Order execution settings."""

    use_market_orders: bool = True
    slippage_bps: float = 20.0
    post_only_limit: bool = False
    reduce_only_exits: bool = True
    idempotent_order_link: bool = True


class BurnInConfig(BaseModel):
    """Burn-in / validation mode: stricter limits and validation artifacts."""

    burn_in_enabled: bool = False
    burn_in_phase: str = Field(default="demo", pattern="^(demo|testnet|live_small|live_guarded)$")
    burn_in_max_trades_per_day: int = Field(default=20, ge=1, le=200)
    burn_in_max_notional_usdt: float = Field(default=5_000.0, ge=100, le=500_000)
    burn_in_required_report_window_hours: float = Field(default=24.0, ge=1, le=168)
    burn_in_min_expected_heartbeat_coverage: float = Field(default=0.8, ge=0, le=1)
    burn_in_fail_on_protection_mismatch: bool = True
    burn_in_fail_on_execution_drift: bool = True
    burn_in_max_slippage_bps: float = Field(default=50.0, ge=5, le=500)
    burn_in_max_reconnect_per_hour: int = Field(default=5, ge=0, le=50)


class LoggingConfig(BaseModel):
    """Logging settings."""

    level: str = "INFO"
    structured: bool = True
    log_file: Optional[str] = None
    rotation: str = "10 MB"
    retention: str = "7 days"


class Config(BaseModel):
    """Main application configuration."""

    mode: str = Field(default="paper", pattern="^(dry_run|paper|live)$")
    dry_run: bool = False
    demo_mode: bool = False
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    entry: EntryThresholds = Field(default_factory=EntryThresholds)
    stop_tp: StopTPConfig = Field(default_factory=StopTPConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    burn_in: BurnInConfig = Field(default_factory=BurnInConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    portfolio_exposure: PortfolioExposureConfig = Field(default_factory=PortfolioExposureConfig)
    database_path: str = "data/bot.db"
    scan_interval_seconds: float = 5.0
    score_interval_seconds: float = Field(default=5.0, ge=1)
    health_check_interval_seconds: float = 30.0
    stage4_enabled: bool = True
    stage5_enabled: bool = True
    active_strategy: str = Field(default="flow_impulse", pattern="^(flow_impulse)$")
    # Context refresh (seconds)
    kline_refresh_seconds: float = Field(default=60, ge=30)
    oi_refresh_seconds: float = Field(default=60, ge=30)
    funding_refresh_seconds: float = Field(default=300, ge=60)
    long_short_ratio_refresh_seconds: float = Field(default=300, ge=60)
    instrument_refresh_seconds: float = Field(default=600, ge=300)
    context_staleness_seconds: float = Field(default=120, ge=60)
    # WS
    public_ws_max_symbols_per_connection: int = Field(default=50, ge=1, le=200)
    public_ws_stale_timeout_seconds: float = Field(default=90, ge=30)
    private_ws_stale_timeout_seconds: float = Field(default=120, ge=30)
    # Reconciliation
    rest_reconciliation_interval_seconds: float = Field(default=60, ge=30)
    recover_orphan_positions: bool = True
    emergency_flatten_on_startup: bool = False
    # Protection / trailing / shards
    trailing_stop_update_seconds: float = Field(default=15.0, ge=5)
    shard_reconnect_backoff_seconds: float = Field(default=30.0, ge=5)
    repair_missing_protection_on_startup: bool = True
    startup_protection_required: bool = False
    runner_trailing_enabled: bool = True


def load_config(config_path: Optional[Path] = None) -> tuple[Config, EnvSettings]:
    """Load configuration from YAML and .env."""
    config_path = config_path or Path("config/config.yaml")
    env = EnvSettings()

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using defaults")
        return Config(), env

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Override exchange testnet from env: only true when env is testnet (demo and live use mainnet endpoints)
    data.setdefault("exchange", {})
    data["exchange"]["testnet"] = get_bybit_env(env) == "testnet"

    config = Config.model_validate(data)
    if config.mode == "dry_run":
        config.dry_run = True
    if config.mode == "paper":
        config.demo_mode = True
    return config, env
