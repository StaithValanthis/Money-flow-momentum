"""Configuration loading and validation using Pydantic."""

from pathlib import Path
from typing import Optional, Literal

import yaml
from dotenv import load_dotenv, dotenv_values
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from src.utils.logging import get_logger

# Load default .env only when no instance-specific env is used (see load_config)
load_dotenv()

logger = get_logger(__name__)

OperatingModeType = Literal["demo_research", "live_guarded"]
OPERATING_MODE_DEMO_RESEARCH: OperatingModeType = "demo_research"
OPERATING_MODE_LIVE_GUARDED: OperatingModeType = "live_guarded"
BybitEnvType = Literal["demo", "live", "testnet"]

# Canonical instance names for dual-instance operation
INSTANCE_DEMO = "demo"
INSTANCE_LIVE = "live"


def instance_from_config_path(config_path: Optional[Path]) -> Optional[str]:
    """
    Derive instance name from config file path for dual-instance isolation.
    config.config.demo.yaml / config.demo.yaml -> demo
    config.config.live.yaml / config.live.yaml -> live
    """
    if not config_path:
        return None
    path = Path(config_path)
    name = path.name.lower()
    if ".demo." in name or name == "config.demo.yaml" or name.endswith(".demo.yaml"):
        return INSTANCE_DEMO
    if ".live." in name or name == "config.live.yaml" or name.endswith(".live.yaml"):
        return INSTANCE_LIVE
    return None


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


def _env_settings_from_file(env_file_path: Path) -> EnvSettings:
    """
    Build EnvSettings from a single file only (no os.environ merge).
    Use when env_file_path is explicitly provided (e.g. tests, promote-env) so
    the file is the single source of truth.
    """
    raw = dotenv_values(env_file_path, encoding="utf-8") or {}
    def s(key: str) -> str:
        return (raw.get(key) or "").strip()
    def b(key: str) -> bool:
        v = (raw.get(key) or "").strip().lower()
        return v in ("1", "true", "yes")
    return EnvSettings(
        bybit_env=s("BYBIT_ENV") or "demo",
        bybit_api_key=s("BYBIT_API_KEY"),
        bybit_api_secret=s("BYBIT_API_SECRET"),
        bybit_testnet=b("BYBIT_TESTNET") if "BYBIT_TESTNET" in raw else True,
        bybit_demo_api_key=s("BYBIT_DEMO_API_KEY"),
        bybit_demo_api_secret=s("BYBIT_DEMO_API_SECRET"),
        bybit_live_api_key=s("BYBIT_LIVE_API_KEY"),
        bybit_live_api_secret=s("BYBIT_LIVE_API_SECRET"),
        bybit_testnet_api_key=s("BYBIT_TESTNET_API_KEY"),
        bybit_testnet_api_secret=s("BYBIT_TESTNET_API_SECRET"),
    )


def get_bybit_env(env: EnvSettings) -> BybitEnvType:
    """Resolve effective environment: BYBIT_ENV if set, else BYBIT_TESTNET -> testnet/live."""
    e = (getattr(env, "bybit_env", "") or "").strip().lower()
    if e in ("demo", "live", "testnet"):
        return e  # type: ignore
    if getattr(env, "bybit_testnet", True):
        return "testnet"
    return "live"


def resolve_bybit_credentials(env: "EnvSettings", env_type: Optional[BybitEnvType] = None) -> tuple[str, str, bool, BybitEnvType]:
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
    burn_in_max_trades_per_day: int = Field(default=20, ge=1, le=2000)
    burn_in_max_notional_usdt: float = Field(default=5_000.0, ge=100, le=2_000_000)
    burn_in_required_report_window_hours: float = Field(default=24.0, ge=1, le=168)
    burn_in_min_expected_heartbeat_coverage: float = Field(default=0.8, ge=0, le=1)
    burn_in_fail_on_protection_mismatch: bool = True
    burn_in_fail_on_execution_drift: bool = True
    burn_in_max_slippage_bps: float = Field(default=50.0, ge=5, le=500)
    burn_in_max_reconnect_per_hour: int = Field(default=5, ge=0, le=50)


class AutomationConfig(BaseModel):
    """Automation / orchestration for Demo burn-in and optimization."""

    enabled: bool = False
    demo_orchestration_enabled: bool = False
    # Cadence / scheduling
    readiness_check_interval_seconds: float = Field(default=900.0, ge=60.0, le=86400.0)
    min_trades_for_auto_evaluation: int = Field(default=50, ge=0)
    min_hours_between_evaluations: float = Field(default=6.0, ge=0.5, le=168.0)
    min_hours_between_optimizer_runs: float = Field(default=24.0, ge=1.0, le=720.0)
    # Behaviour flags
    auto_start_shadow_for_best_candidate: bool = True
    require_readiness_for_optimizer: bool = True
    pause_on_kill_switch: bool = True
    pause_on_burnin_gate_breach: bool = True
    # Demo-only auto-adopt: automatically activate a better candidate as Demo active config (never touches Live)
    auto_adopt_demo_candidates: bool = False
    min_trades_for_demo_adoption: int = Field(default=50, ge=10, le=10000)
    min_hours_between_demo_adoptions: float = Field(default=24.0, ge=1.0, le=720.0)
    require_shadow_before_demo_adoption: bool = False


class DemoResearchConfig(BaseModel):
    """Demo-only research mode: fixed synthetic equity, relaxed kill switch, permissive burn-in. Only active when operating_mode == demo_research."""

    fixed_equity_enabled: bool = False
    fixed_equity_usdt: float = Field(default=1000.0, ge=100.0, le=1_000_000.0)
    relaxed_kill_switch_enabled: bool = False
    demo_max_daily_drawdown_pct: float = Field(default=15.0, ge=5.0, le=50.0)
    demo_max_daily_realized_loss_usdt: float = Field(default=150.0, ge=50.0, le=10_000.0)
    demo_research_burnin_permissive: bool = True


class ResearchPolicyConfig(BaseModel):
    """Demo-only research verdict thresholds and reporting policy."""

    enabled: bool = True

    # When to stop calling it "too early"
    min_total_warm_start_candidates_before_strategy_judgment: int = Field(default=500, ge=0, le=1_000_000)
    min_real_demo_closed_trades_before_strategy_judgment: int = Field(default=200, ge=0, le=1_000_000)
    min_completed_eval_cycles_before_strategy_judgment: int = Field(default=3, ge=0, le=10_000)

    # What counts as a surviving / reviewable Demo candidate
    min_demo_profit_factor_for_candidate_review: float = Field(default=1.10, ge=0.0, le=10.0)
    min_demo_expectancy_for_candidate_review: float = Field(default=0.0)
    max_demo_ultra_short_trade_fraction_for_candidate_review: float = Field(default=0.25, ge=0.0, le=1.0)

    # Optional strategy-doubt threshold behavior
    require_no_surviving_candidate_after_thresholds: bool = True

    # Optional behavior hooks
    emit_verdict_in_status: bool = True
    emit_verdict_artifact: bool = True


class WarmStartConfig(BaseModel):
    """
    Demo-only warm-start: seed Demo from historical candle calibration before first trading.
    Ignored when operating_mode != demo_research. Never touches Live.
    Seed acceptance thresholds apply only to warm-start replay winner before Demo activation.
    """

    enabled: bool = True
    auto_seed_demo_on_fresh_install: bool = True
    min_local_trades_to_skip_warm_start: int = Field(default=50, ge=0, le=10000)
    candle_source: str = Field(default="exchange", pattern="^(local|exchange|local_or_exchange)$")
    lookback_days: int = Field(default=7, ge=1, le=365, description="Days of candle history for warm-start (default 7 for VM startup)")
    timeframe: str = Field(default="5", description="Bybit interval: 1, 3, 5, 15, 30, 60, D, W, M")
    symbols_limit: int = Field(default=10, ge=1, le=200, description="Max symbols for warm-start (default 10 for bounded runtime)")
    require_profitable_seed: bool = True
    fallback_to_safe_seed_on_failure: bool = True
    n_samples: int = Field(default=8, ge=3, le=100, description="Candidate parameter sets to replay-evaluate when search_until_viable=False")
    max_runtime_seconds: int = Field(default=300, ge=30, le=3600, description="Hard limit per run when search_until_viable=False")

    # Iterative search-until-viable (Demo-only)
    search_until_viable: bool = Field(default=False, description="If True, run batches until a viable seed is found or budget exhausted")
    batch_n_samples: int = Field(default=8, ge=2, le=100, description="Candidates per batch when search_until_viable=True")
    max_batches: int = Field(default=10, ge=1, le=100, description="Max search batches when search_until_viable=True")
    max_total_runtime_seconds: int = Field(default=1800, ge=60, le=7200, description="Total wall-clock limit across all batches")
    require_viable_seed_before_trading: bool = Field(
        default=False,
        description="If True, Demo must not start unless warm-start found a viable seed (no silent fallback)",
    )
    allow_fallback_if_no_viable_seed: bool = Field(
        default=True,
        description="When search exhausted with no viable seed: if True activate fallback; if False do not start trading",
    )

    # Backtest-style historical evaluation costs (Demo-only; do not affect Live runtime)
    backtest_fee_bps: float = Field(
        default=6.0,
        ge=0.0,
        le=100.0,
        description="Per-side fee in basis points for backtest-style warm-start evaluation",
    )
    backtest_slippage_bps: float = Field(
        default=2.0,
        ge=0.0,
        le=100.0,
        description="Per-side slippage estimate in basis points for backtest-style warm-start evaluation",
    )

    # Seed acceptance (Demo-only): replay winner must pass these before auto-activation
    min_replay_trade_count: int = Field(default=30, ge=1, le=10000, description="Min closed trades in replay to accept seed")
    min_win_rate: float = Field(default=0.18, ge=0.0, le=1.0, description="Min win rate to accept seed")
    min_profit_factor: float = Field(default=1.10, ge=1.0, le=10.0, description="Min profit factor to accept seed")
    min_payoff_ratio: float = Field(default=1.20, ge=0.0, le=20.0, description="Min payoff ratio (avg_win/|avg_loss|) to accept seed")
    max_replay_drawdown: float = Field(default=10.0, ge=0.0, le=100.0, description="Max replay drawdown % to accept seed")
    min_median_trade_duration_sec: float = Field(default=120.0, ge=0.0, le=86400.0, description="Min median trade duration (entry->exit) seconds")
    ultra_short_duration_sec: float = Field(default=60.0, ge=0.0, le=600.0, description="Trades shorter than this are 'ultra-short' for churn checks")
    max_ultra_short_trade_fraction: float = Field(default=0.25, ge=0.0, le=1.0, description="Max fraction of trades that may be ultra-short")
    reject_zero_fee_zero_slippage_only_edges: bool = Field(
        default=True,
        description="When replay has no fees/slippage, reject if edge is razor-thin (require min margin above breakeven)",
    )
    min_profit_margin_pct_when_zero_fee: float = Field(
        default=0.5, ge=0.0, le=50.0,
        description="When reject_zero_fee_zero_slippage_only_edges: min return_pct required when fees/slippage are zero",
    )

    # Protection-aware acceptance (Demo-only): reject seeds with poor exit behavior
    max_stop_out_rate: float = Field(
        default=0.55, ge=0.0, le=1.0,
        description="Max fraction of exits that may be stop-loss before seed is rejected",
    )
    max_consecutive_losses: int = Field(
        default=6, ge=1, le=50,
        description="Max allowed consecutive losing trades before seed is rejected",
    )
    min_tp1_hit_rate: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description="Min fraction of exits that must be TP1 (or better) before seed is accepted",
    )


class DemoProbationConfig(BaseModel):
    """Demo-only probation: historically passable seed must pass real Demo validation before trusted baseline."""

    enabled: bool = True

    # Probation sample size
    min_closed_trades: int = Field(default=30, ge=1, le=10_000)
    min_runtime_minutes: int = Field(default=60, ge=1, le=100_000)

    # Failure conditions
    forbid_kill_switch_hit: bool = True
    max_consecutive_losses: int = Field(default=5, ge=1, le=50)
    max_stop_out_rate: float = Field(default=0.50, ge=0.0, le=1.0)
    max_ultra_short_trade_fraction: float = Field(default=0.25, ge=0.0, le=1.0)

    # Minimum success criteria
    min_profit_factor: float = Field(default=1.05, ge=0.0, le=20.0)
    min_expectancy: float = Field(default=0.0)

    # Promotion behavior
    auto_promote_probation_pass_to_active_demo: bool = True
    auto_reject_on_failure: bool = True

    # Startup behavior
    allow_demo_trading_with_probation_candidate: bool = True

    # Failure action: stop Demo runtime on probation failure (Demo-only)
    stop_demo_on_failure: bool = True

    # Fail-fast: reject obviously failed candidates immediately / on next tick (Demo-only)
    fail_fast_on_kill_switch: bool = True
    fail_fast_on_hard_block: bool = True
    no_trade_stall_minutes: int = Field(default=10, ge=1, le=10080)  # 1 week max
    fail_if_stalled_and_negative_expectancy: bool = True
    fail_if_stalled_and_pf_below: float = Field(default=0.90, ge=0.0, le=2.0)
    auto_reinit_after_failure: bool = False


class LoggingConfig(BaseModel):
    """Logging settings."""

    level: str = "INFO"
    structured: bool = True
    log_file: Optional[str] = None
    rotation: str = "10 MB"
    retention: str = "7 days"


class Config(BaseModel):
    """Main application configuration."""

    operating_mode: Optional[Literal["demo_research", "live_guarded"]] = Field(
        default=None,
        description="Top-level mode: demo_research (autonomous Demo) or live_guarded (guarded Live). If unset, derived from env/automation/burn_in.",
    )
    # Dual-instance: instance_name scopes DB, artifacts, logs when set (demo | live)
    instance_name: Optional[str] = Field(
        default=None,
        description="Instance identity for dual-instance operation: 'demo' or 'live'. Derived from config path if not set.",
    )
    mode: str = Field(default="paper", pattern="^(dry_run|paper|live)$")
    dry_run: bool = False
    # demo_mode: deprecated for execution; do not set from mode=paper. Use dry_run to control simulated vs real orders.
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
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    demo_research: DemoResearchConfig = Field(default_factory=DemoResearchConfig)
    research_policy: ResearchPolicyConfig = Field(default_factory=ResearchPolicyConfig)
    warm_start: WarmStartConfig = Field(default_factory=WarmStartConfig)
    demo_probation: DemoProbationConfig = Field(default_factory=DemoProbationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    portfolio_exposure: PortfolioExposureConfig = Field(default_factory=PortfolioExposureConfig)
    database_path: str = "data/bot.db"
    # Instance-scoped roots (set by load_config when instance_name is set)
    artifacts_root: str = "artifacts"
    logs_dir: str = "logs"
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


def get_effective_operating_mode(config: Config, env: EnvSettings) -> OperatingModeType:
    """Return the effective operating mode (explicit or derived). Used for display and branching."""
    if config.operating_mode is not None:
        return config.operating_mode
    env_t = get_bybit_env(env)
    if env_t == "live":
        return OPERATING_MODE_LIVE_GUARDED
    if env_t == "demo":
        auto = getattr(config, "automation", None)
        burn = getattr(config, "burn_in", None)
        if (
            auto
            and getattr(auto, "enabled", False)
            and getattr(auto, "demo_orchestration_enabled", False)
            and burn
            and getattr(burn, "burn_in_enabled", False)
            and (getattr(burn, "burn_in_phase", "") or "") == "demo"
        ):
            return OPERATING_MODE_DEMO_RESEARCH
    return OPERATING_MODE_LIVE_GUARDED


def normalize_operating_mode(config: Config, env: EnvSettings) -> None:
    """
    Apply operating mode semantics. If operating_mode is set explicitly, derive burn_in and automation
    to match. If unset, infer mode from env/automation/burn_in and set it on config for display.
    """
    effective = get_effective_operating_mode(config, env)
    if config.operating_mode is not None:
        if config.operating_mode == OPERATING_MODE_DEMO_RESEARCH:
            config.burn_in.burn_in_enabled = True
            config.burn_in.burn_in_phase = "demo"
            config.burn_in.burn_in_max_trades_per_day = max(
                config.burn_in.burn_in_max_trades_per_day, 200
            )
            config.burn_in.burn_in_max_notional_usdt = max(
                config.burn_in.burn_in_max_notional_usdt, 500_000.0
            )
            dr = getattr(config, "demo_research", None)
            if dr and getattr(dr, "demo_research_burnin_permissive", True):
                config.burn_in.burn_in_max_trades_per_day = max(
                    config.burn_in.burn_in_max_trades_per_day, 500
                )
                config.burn_in.burn_in_max_notional_usdt = max(
                    config.burn_in.burn_in_max_notional_usdt, 1_000_000.0
                )
            config.automation.enabled = True
            config.automation.demo_orchestration_enabled = True
            config.automation.auto_start_shadow_for_best_candidate = True
            # Demo-only research risk profile: if fixed-equity mode is enabled, cap notionals relative to fixed equity
            dr = getattr(config, "demo_research", None)
            if dr and getattr(dr, "fixed_equity_enabled", False):
                fixed_eq = float(getattr(dr, "fixed_equity_usdt", 1000.0))
                # Per-symbol notional cap: at most 1x fixed equity (unless user already set a lower value)
                if config.risk.max_notional_per_symbol_usdt > fixed_eq:
                    config.risk.max_notional_per_symbol_usdt = max(fixed_eq, config.risk.min_notional_per_trade_usdt)
                # Portfolio notional cap: at most 3x fixed equity (unless user already set a lower value)
                max_portfolio_cap = fixed_eq * 3.0
                if config.risk.max_portfolio_notional_usdt <= 0:
                    config.risk.max_portfolio_notional_usdt = max_portfolio_cap
                elif config.risk.max_portfolio_notional_usdt > max_portfolio_cap:
                    config.risk.max_portfolio_notional_usdt = max_portfolio_cap
        else:
            # live_guarded: stricter effective profile than demo_research
            config.burn_in.burn_in_phase = "live_guarded"
            config.automation.demo_orchestration_enabled = False
            # Apply stricter burn-in caps only when current values are permissive (>= demo defaults)
            if config.burn_in.burn_in_max_trades_per_day >= 200:
                config.burn_in.burn_in_max_trades_per_day = min(
                    config.burn_in.burn_in_max_trades_per_day, 50
                )
            if config.burn_in.burn_in_max_notional_usdt >= 500_000.0:
                config.burn_in.burn_in_max_notional_usdt = min(
                    config.burn_in.burn_in_max_notional_usdt, 20_000.0
                )
    config.operating_mode = effective


def get_effective_equity_for_sizing(config: Config, env: EnvSettings, fetched_equity_usdt: float) -> float:
    """
    Return the equity to use for risk sizing and allocator.
    When operating_mode == demo_research and demo_research.fixed_equity_enabled, returns fixed_equity_usdt;
    otherwise returns fetched_equity_usdt. Live always uses fetched (actual).
    """
    mode = get_effective_operating_mode(config, env)
    if mode != OPERATING_MODE_DEMO_RESEARCH:
        return fetched_equity_usdt
    dr = getattr(config, "demo_research", None)
    if dr and getattr(dr, "fixed_equity_enabled", False):
        return float(getattr(dr, "fixed_equity_usdt", 1000.0))
    return fetched_equity_usdt


def get_demo_research_runtime_info(config: Config, env: EnvSettings) -> dict:
    """Return dict with fixed_equity_enabled, effective_equity_source, effective_strategy_equity_usdt (when fixed), relaxed_kill_switch_enabled."""
    mode = get_effective_operating_mode(config, env)
    dr = getattr(config, "demo_research", None)
    out = {
        "fixed_equity_enabled": False,
        "effective_equity_source": "actual",
        "effective_strategy_equity_usdt": None,
        "relaxed_kill_switch_enabled": False,
    }
    if mode != OPERATING_MODE_DEMO_RESEARCH or not dr:
        return out
    out["fixed_equity_enabled"] = getattr(dr, "fixed_equity_enabled", False)
    out["effective_equity_source"] = "fixed" if out["fixed_equity_enabled"] else "actual"
    if out["fixed_equity_enabled"]:
        out["effective_strategy_equity_usdt"] = float(getattr(dr, "fixed_equity_usdt", 1000.0))
    out["relaxed_kill_switch_enabled"] = getattr(dr, "relaxed_kill_switch_enabled", False)
    return out

def load_config(
    config_path: Optional[Path] = None,
    env_file_path: Optional[Path] = None,
) -> tuple[Config, EnvSettings]:
    """Load configuration from YAML and env. For dual-instance, use config.config.demo.yaml or config.config.live.yaml."""
    config_path = config_path or Path("config/config.yaml")
    # Derive instance from path for dual-instance path scoping
    instance = instance_from_config_path(config_path)
    # Load env: explicit file, else .env.{instance}, else .env (override so per-call env wins)
    if env_file_path is not None and Path(env_file_path).exists():
        env = _env_settings_from_file(Path(env_file_path))
    elif env_file_path is not None:
        # Explicit path given but missing: fall back to normal load
        if instance:
            load_dotenv(Path(f".env.{instance}"), override=True)
        else:
            load_dotenv(Path(".env"), override=True)
        env = EnvSettings()
    elif instance:
        load_dotenv(Path(f".env.{instance}"), override=True)
        env = EnvSettings()
    else:
        load_dotenv(Path(".env"), override=True)
        env = EnvSettings()

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using defaults")
        config = Config()
        if instance:
            config.instance_name = instance
            config.artifacts_root = f"artifacts/{instance}"
            config.logs_dir = f"logs/{instance}"
            config.database_path = f"data/{instance}/bot.db"
        normalize_operating_mode(config, env)
        return config, env

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Explicit instance_name in YAML overrides path-based derivation
    if data.get("instance_name") in ("demo", "live"):
        instance = data["instance_name"]
    data.setdefault("operating_mode", None)
    if data.get("operating_mode") not in ("demo_research", "live_guarded"):
        data["operating_mode"] = None

    # Override exchange testnet from env
    data.setdefault("exchange", {})
    data["exchange"]["testnet"] = get_bybit_env(env) == "testnet"

    config = Config.model_validate(data)
    if config.mode == "dry_run":
        config.dry_run = True
    normalize_operating_mode(config, env)

    # Apply instance-scoped paths when instance is set (from path or YAML)
    if instance:
        config.instance_name = instance
        if config.artifacts_root == "artifacts":
            config.artifacts_root = f"artifacts/{instance}"
        if config.logs_dir == "logs":
            config.logs_dir = f"logs/{instance}"
        if config.database_path == "data/bot.db":
            config.database_path = f"data/{instance}/bot.db"
    return config, env
