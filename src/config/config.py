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


class EnvSettings(BaseSettings):
    """Secrets and env-only settings."""

    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_testnet: bool = True

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


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


class RiskConfig(BaseModel):
    """Risk management settings."""

    risk_per_trade_pct: float = Field(default=0.5, ge=0.1, le=2.0)
    max_concurrent_positions: int = Field(default=5, ge=1, le=20)
    max_total_risk_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    max_daily_drawdown_pct: float = Field(default=5.0, ge=1.0, le=20.0)
    max_notional_per_symbol_usdt: float = 10_000.0
    min_notional_per_trade_usdt: float = 10.0
    cooldown_after_loss_seconds: int = 300
    kill_switch_enabled: bool = True
    stale_data_seconds: float = 60.0


class ExecutionConfig(BaseModel):
    """Order execution settings."""

    use_market_orders: bool = True
    slippage_bps: float = 20.0
    post_only_limit: bool = False
    reduce_only_exits: bool = True
    idempotent_order_link: bool = True


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
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    entry: EntryThresholds = Field(default_factory=EntryThresholds)
    stop_tp: StopTPConfig = Field(default_factory=StopTPConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    database_path: str = "data/bot.db"
    scan_interval_seconds: float = 5.0
    health_check_interval_seconds: float = 30.0


def load_config(config_path: Optional[Path] = None) -> tuple[Config, EnvSettings]:
    """Load configuration from YAML and .env."""
    config_path = config_path or Path("config/config.yaml")
    env = EnvSettings()

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using defaults")
        return Config(), env

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Override testnet from env
    data.setdefault("exchange", {})
    data["exchange"]["testnet"] = env.bybit_testnet

    config = Config.model_validate(data)
    return config, env
