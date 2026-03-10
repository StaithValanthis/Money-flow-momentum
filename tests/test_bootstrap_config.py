"""Tests for bootstrap_config.py: config generation and burn-in defaults."""

import pytest
import yaml

from bootstrap_config import build_config_yaml


def test_bootstrap_demo_writes_burn_in_enabled_and_phase():
    """With BYBIT_ENV=demo, generated config has burn_in enabled and phase demo."""
    content = build_config_yaml("demo", "0.5", "5")
    assert "burn_in_enabled: true" in content
    assert "burn_in_phase: demo" in content
    assert "mode: paper" in content
    assert "dry_run: false" in content


def test_bootstrap_live_writes_burn_in_live_small():
    """With BYBIT_ENV=live, generated config has burn_in enabled and phase live_small."""
    content = build_config_yaml("live", "0.5", "5")
    assert "burn_in_enabled: true" in content
    assert "burn_in_phase: live_small" in content


def test_bootstrap_testnet_writes_burn_in_testnet():
    """With BYBIT_ENV=testnet, generated config has burn_in phase testnet."""
    content = build_config_yaml("testnet", "0.5", "5")
    assert "burn_in_enabled: true" in content
    assert "burn_in_phase: testnet" in content


def test_bootstrap_config_is_valid_yaml():
    """Generated config parses as YAML and has expected structure."""
    for env in ("demo", "live", "testnet"):
        content = build_config_yaml(env, "0.5", "5")
        data = yaml.safe_load(content)
        assert data["mode"] == "paper"
        assert data.get("dry_run") is False
        assert data["burn_in"]["burn_in_enabled"] is True
        assert data["burn_in"]["burn_in_phase"] in ("demo", "live_small", "testnet")
        assert data["risk"]["risk_per_trade_pct"] in (0.5, "0.5")
        assert data["risk"]["max_concurrent_positions"] == 5
