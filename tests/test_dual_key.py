"""Tests for dual-key Bybit credential handling (demo/live/testnet). No exchange access."""

import os
from pathlib import Path

import pytest

from src.config.config import EnvSettings, resolve_bybit_credentials, get_bybit_env


def test_resolve_demo_uses_demo_keys_when_set():
    """When env_type=demo and demo dual-key set, return demo keys and is_legacy=False."""
    env = EnvSettings(
        bybit_env="demo",
        bybit_demo_api_key="dk",
        bybit_demo_api_secret="ds",
        bybit_live_api_key="lk",
        bybit_live_api_secret="ls",
    )
    key, secret, is_legacy, eff = resolve_bybit_credentials(env, "demo")
    assert key == "dk"
    assert secret == "ds"
    assert is_legacy is False
    assert eff == "demo"


def test_resolve_live_uses_live_keys_when_set():
    """When env_type=live and live dual-key set, return live keys and is_legacy=False."""
    env = EnvSettings(
        bybit_env="live",
        bybit_demo_api_key="dk",
        bybit_demo_api_secret="ds",
        bybit_live_api_key="lk",
        bybit_live_api_secret="ls",
    )
    key, secret, is_legacy, eff = resolve_bybit_credentials(env, "live")
    assert key == "lk"
    assert secret == "ls"
    assert is_legacy is False
    assert eff == "live"


def test_resolve_testnet_uses_testnet_keys_when_set():
    """When env_type=testnet and testnet dual-key set, return testnet keys."""
    env = EnvSettings(
        bybit_env="testnet",
        bybit_testnet_api_key="tk",
        bybit_testnet_api_secret="ts",
        bybit_live_api_key="lk",
        bybit_live_api_secret="ls",
    )
    key, secret, is_legacy, eff = resolve_bybit_credentials(env, "testnet")
    assert key == "tk"
    assert secret == "ts"
    assert is_legacy is False
    assert eff == "testnet"


def test_resolve_demo_fallback_legacy():
    """When env_type=demo and only legacy keys set, return legacy and is_legacy=True."""
    env = EnvSettings(
        bybit_env="demo",
        bybit_api_key="legacy_k",
        bybit_api_secret="legacy_s",
    )
    key, secret, is_legacy, eff = resolve_bybit_credentials(env, "demo")
    assert key == "legacy_k"
    assert secret == "legacy_s"
    assert is_legacy is True
    assert eff == "demo"


def test_resolve_live_fallback_legacy():
    """When env_type=live and only legacy keys set, return legacy and is_legacy=True."""
    env = EnvSettings(
        bybit_env="live",
        bybit_api_key="legacy_k",
        bybit_api_secret="legacy_s",
    )
    key, secret, is_legacy, eff = resolve_bybit_credentials(env, "live")
    assert key == "legacy_k"
    assert secret == "legacy_s"
    assert is_legacy is True


def test_resolve_demo_empty_when_only_live_dual_key():
    """When env_type=demo and only live dual-key set (no legacy), return empty."""
    env = EnvSettings(
        bybit_env="demo",
        bybit_live_api_key="lk",
        bybit_live_api_secret="ls",
    )
    key, secret, is_legacy, _ = resolve_bybit_credentials(env, "demo")
    assert key == ""
    assert secret == ""
    assert is_legacy is False


def test_resolve_live_empty_when_only_demo_dual_key():
    """When env_type=live and only demo dual-key set (no legacy), return empty."""
    env = EnvSettings(
        bybit_env="live",
        bybit_demo_api_key="dk",
        bybit_demo_api_secret="ds",
    )
    key, secret, is_legacy, _ = resolve_bybit_credentials(env, "live")
    assert key == ""
    assert secret == ""
    assert is_legacy is False


def test_get_bybit_env_from_bybit_env_var():
    """get_bybit_env returns BYBIT_ENV when set to demo/live/testnet."""
    assert get_bybit_env(EnvSettings(bybit_env="demo")) == "demo"
    assert get_bybit_env(EnvSettings(bybit_env="live")) == "live"
    assert get_bybit_env(EnvSettings(bybit_env="testnet")) == "testnet"


def test_get_bybit_env_fallback_testnet_true():
    """get_bybit_env returns testnet when BYBIT_TESTNET=true and BYBIT_ENV not set."""
    env = EnvSettings(bybit_env="", bybit_testnet=True)
    assert get_bybit_env(env) == "testnet"


def test_get_bybit_env_fallback_live():
    """get_bybit_env returns live when BYBIT_TESTNET=false and BYBIT_ENV not set."""
    env = EnvSettings(bybit_env="", bybit_testnet=False)
    assert get_bybit_env(env) == "live"


def test_resolve_with_none_uses_get_bybit_env():
    """resolve_bybit_credentials(env, None) uses get_bybit_env(env) for env_type."""
    env = EnvSettings(bybit_env="demo", bybit_demo_api_key="dk", bybit_demo_api_secret="ds")
    key, secret, is_legacy, eff = resolve_bybit_credentials(env, None)
    assert key == "dk"
    assert secret == "ds"
    assert eff == "demo"


def test_validation_passes_demo_dual_key(tmp_path):
    """Validation passes when demo selected and demo dual-key set."""
    from src.cli.validate_env import validate_environment
    (tmp_path / "config").mkdir()
    cfg = tmp_path / "config" / "config.yaml"
    cfg.write_text("""
mode: paper
exchange:
  testnet: false
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
database_path: """ + str((tmp_path / "data" / "bot.db").as_posix()) + """
""")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "artifacts" / "burnin").mkdir(parents=True)
    (tmp_path / "artifacts" / "validation").mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "BYBIT_ENV=demo\nBYBIT_DEMO_API_KEY=dk\nBYBIT_DEMO_API_SECRET=ds\n"
    )
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = validate_environment(config_path=cfg, require_api_keys_for_live=True)
        assert result.ok is True, result.errors
    finally:
        os.chdir(orig)


def test_validation_passes_live_dual_key(tmp_path):
    """Validation passes when live selected and live dual-key set."""
    from src.cli.validate_env import validate_environment
    (tmp_path / "config").mkdir()
    cfg = tmp_path / "config" / "config.yaml"
    cfg.write_text("""
mode: live
exchange:
  testnet: false
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
database_path: """ + str((tmp_path / "data" / "bot.db").as_posix()) + """
""")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "artifacts" / "burnin").mkdir(parents=True)
    (tmp_path / "artifacts" / "validation").mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "BYBIT_ENV=live\nBYBIT_LIVE_API_KEY=lk\nBYBIT_LIVE_API_SECRET=ls\n"
    )
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = validate_environment(config_path=cfg, require_api_keys_for_live=True)
        assert result.ok is True, result.errors
    finally:
        os.chdir(orig)


def test_validation_fails_demo_selected_but_only_live_keys(tmp_path):
    """Validation fails when BYBIT_ENV=demo but only live dual-key set (no legacy)."""
    from src.cli.validate_env import validate_environment
    (tmp_path / "config").mkdir()
    cfg = tmp_path / "config" / "config.yaml"
    cfg.write_text("""
mode: paper
exchange:
  testnet: false
universe:
  min_24h_turnover_usdt: 1000000
  max_spread_bps: 50
risk:
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 5
database_path: """ + str((tmp_path / "data" / "bot.db").as_posix()) + """
""")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "artifacts" / "burnin").mkdir(parents=True)
    (tmp_path / "artifacts" / "validation").mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "BYBIT_ENV=demo\nBYBIT_LIVE_API_KEY=lk\nBYBIT_LIVE_API_SECRET=ls\n"
    )
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = validate_environment(config_path=cfg, require_api_keys_for_live=True)
        assert result.ok is False
        assert any("demo" in e.lower() and "live" in e.lower() for e in result.errors) or \
               any("credentials" in e.lower() or "DEMO" in e for e in result.errors)
    finally:
        os.chdir(orig)


def test_show_runtime_mode_reports_dual_key_or_legacy():
    """show-runtime-mode CLI reports credential_mode and selected_key_pair (no secrets)."""
    root = Path(__file__).resolve().parents[1]
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        pytest.skip("config/config.yaml not present")
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "run_bot.py", "show-runtime-mode", "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert r.returncode == 0
    assert "selected_environment:" in r.stdout
    assert "credential_mode:" in r.stdout
    assert "selected_key_pair:" in r.stdout
    assert "dual_key_configured:" in r.stdout
    # Should show demo= and live= (not testnet= and live=)
    assert "demo=" in r.stdout
    assert "live=" in r.stdout


def test_env_example_contains_dual_key_vars():
    """.env.example documents BYBIT_ENV and demo/live dual-key variables."""
    root = Path(__file__).resolve().parents[1]
    env_example = root / ".env.example"
    assert env_example.exists()
    text = env_example.read_text(encoding="utf-8")
    assert "BYBIT_ENV" in text
    assert "BYBIT_DEMO_API_KEY" in text
    assert "BYBIT_DEMO_API_SECRET" in text
    assert "BYBIT_LIVE_API_KEY" in text
    assert "BYBIT_LIVE_API_SECRET" in text


def test_bybit_client_demo_mode_uses_demo_and_mainnet_public():
    """BybitClient with demo=True has demo=True, testnet=False (REST/private=demo; public=mainnet)."""
    from src.exchange.bybit_client import BybitClient
    from src.config.config import ExchangeConfig
    client = BybitClient(
        api_key="k",
        api_secret="s",
        testnet=False,
        demo=True,
        config=ExchangeConfig(),
    )
    assert client.demo is True
    assert client.testnet is False
    # Lazy init: http should use demo endpoint (pybit HTTP(demo=True))
    assert client.http is not None
