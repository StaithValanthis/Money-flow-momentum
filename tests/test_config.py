"""Tests for config loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.config.config import load_config, Config, EnvSettings


def test_default_config():
    """Test default config."""
    config, env = load_config(Path("/nonexistent.yaml"))
    assert config.mode == "paper"
    assert config.risk.max_concurrent_positions == 5


def test_config_load():
    """Test loading from file."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump({"mode": "dry_run", "risk": {"max_concurrent_positions": 3}}, f)
        path = Path(f.name)
    try:
        config, _ = load_config(path)
        assert config.mode == "dry_run"
        assert config.risk.max_concurrent_positions == 3
    finally:
        path.unlink()
