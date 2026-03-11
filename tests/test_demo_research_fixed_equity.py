"""Tests for fixed-equity Demo research mode: sizing, kill switch, burn-in, visibility."""

import pytest

from src.config.config import (
    Config,
    EnvSettings,
    DemoResearchConfig,
    get_effective_equity_for_sizing,
    get_demo_research_runtime_info,
    normalize_operating_mode,
    OPERATING_MODE_DEMO_RESEARCH,
    OPERATING_MODE_LIVE_GUARDED,
)
from src.risk.risk_engine import RiskEngine
from src.config.config import RiskConfig


def test_demo_research_fixed_equity_used_for_sizing() -> None:
    """When operating_mode is demo_research and fixed_equity_enabled, effective equity for sizing is fixed."""
    config = Config()
    config.operating_mode = OPERATING_MODE_DEMO_RESEARCH
    config.demo_research = DemoResearchConfig(
        fixed_equity_enabled=True,
        fixed_equity_usdt=1000.0,
    )
    env = EnvSettings()
    env.bybit_env = "demo"
    effective = get_effective_equity_for_sizing(config, env, 50_000.0)
    assert effective == 1000.0


def test_demo_research_fixed_equity_disabled_uses_actual() -> None:
    """When fixed_equity_enabled is False, effective equity is fetched (actual)."""
    config = Config()
    config.operating_mode = OPERATING_MODE_DEMO_RESEARCH
    config.demo_research = DemoResearchConfig(fixed_equity_enabled=False, fixed_equity_usdt=1000.0)
    env = EnvSettings()
    env.bybit_env = "demo"
    effective = get_effective_equity_for_sizing(config, env, 25_000.0)
    assert effective == 25_000.0


def test_live_guarded_ignores_fixed_equity() -> None:
    """live_guarded always uses fetched equity; fixed research equity is ignored."""
    config = Config()
    config.operating_mode = OPERATING_MODE_LIVE_GUARDED
    config.demo_research = DemoResearchConfig(
        fixed_equity_enabled=True,
        fixed_equity_usdt=1000.0,
    )
    env = EnvSettings()
    env.bybit_env = "live"
    effective = get_effective_equity_for_sizing(config, env, 40_000.0)
    assert effective == 40_000.0


def test_demo_relaxed_kill_switch_uses_demo_thresholds() -> None:
    """RiskEngine with demo kill-switch override uses demo thresholds."""
    risk_config = RiskConfig(
        kill_switch_enabled=True,
        max_daily_drawdown_pct=5.0,
        max_daily_realized_loss_usdt=500.0,
    )
    engine = RiskEngine(risk_config, equity_usdt=10_000.0)
    engine.set_daily_start_pnl(10_000.0)
    engine.set_demo_kill_switch_override(max_drawdown_pct=15.0, max_realized_loss_usdt=150.0)
    ok, _ = engine.check_daily_drawdown(9_600.0)
    assert ok is True
    ok, reason = engine.check_daily_drawdown(8_400.0)
    assert ok is False
    assert "15" in reason
    engine.daily_realized_pnl = -100.0
    ok, _ = engine.check_daily_realized_loss()
    assert ok is True
    engine.daily_realized_pnl = -200.0
    ok, reason = engine.check_daily_realized_loss()
    assert ok is False
    assert "150" in reason


def test_live_uses_strict_kill_switch_without_override() -> None:
    """Without override, RiskEngine uses config (strict) thresholds."""
    risk_config = RiskConfig(
        kill_switch_enabled=True,
        max_daily_drawdown_pct=5.0,
        max_daily_realized_loss_usdt=500.0,
    )
    engine = RiskEngine(risk_config, equity_usdt=10_000.0)
    engine.set_daily_start_pnl(10_000.0)
    ok, reason = engine.check_daily_drawdown(9_400.0)
    assert ok is False
    assert "5" in reason


def test_demo_research_burnin_permissive_raises_limits() -> None:
    """When demo_research and demo_research_burnin_permissive, burn-in limits are raised."""
    config = Config()
    config.operating_mode = OPERATING_MODE_DEMO_RESEARCH
    config.burn_in.burn_in_max_trades_per_day = 20
    config.burn_in.burn_in_max_notional_usdt = 5_000.0
    config.demo_research = DemoResearchConfig(demo_research_burnin_permissive=True)
    env = EnvSettings()
    env.bybit_env = "demo"
    normalize_operating_mode(config, env)
    assert config.burn_in.burn_in_max_trades_per_day >= 500
    assert config.burn_in.burn_in_max_notional_usdt >= 1_000_000.0


def test_demo_research_runtime_info_shows_fixed_equity() -> None:
    """get_demo_research_runtime_info returns fixed_equity_enabled when applicable."""
    config = Config()
    config.operating_mode = OPERATING_MODE_DEMO_RESEARCH
    config.demo_research = DemoResearchConfig(
        fixed_equity_enabled=True,
        fixed_equity_usdt=1000.0,
        relaxed_kill_switch_enabled=True,
    )
    env = EnvSettings()
    env.bybit_env = "demo"
    info = get_demo_research_runtime_info(config, env)
    assert info["fixed_equity_enabled"] is True
    assert info["effective_equity_source"] == "fixed"
    assert info["effective_strategy_equity_usdt"] == 1000.0
    assert info["relaxed_kill_switch_enabled"] is True


def test_live_guarded_runtime_info_no_fixed_equity() -> None:
    """For live_guarded, get_demo_research_runtime_info has fixed_equity_enabled False."""
    config = Config()
    config.operating_mode = OPERATING_MODE_LIVE_GUARDED
    config.demo_research = DemoResearchConfig(fixed_equity_enabled=True, fixed_equity_usdt=1000.0)
    env = EnvSettings()
    env.bybit_env = "live"
    info = get_demo_research_runtime_info(config, env)
    assert info["fixed_equity_enabled"] is False
    assert info["effective_equity_source"] == "actual"
    assert info["effective_strategy_equity_usdt"] is None


def test_risk_engine_sizing_uses_equity_from_set_equity() -> None:
    """Position sizing uses the equity set via set_equity (fixed in demo research)."""
    risk_config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(risk_config, equity_usdt=10_000.0)
    engine.set_equity(1000.0)
    result = engine.compute_position_size(
        symbol="BTCUSDT",
        side="Buy",
        entry_price=50_000.0,
        stop_price=49_000.0,
        qty_step=0.001,
        min_qty=0.001,
        min_notional=10.0,
        max_notional=100_000.0,
    )
    assert result.reject_reason is None
    assert result.risk_usdt == pytest.approx(10.0, rel=0.01)
