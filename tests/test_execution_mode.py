"""Tests for execution mode: dry_run vs real orders, mode=paper vs dry_run vs live."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.config.config import load_config


def test_mode_dry_run_sets_dry_run_true():
    """mode=dry_run => dry_run is True (simulate only)."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump({"mode": "dry_run"}, f)
        path = Path(f.name)
    try:
        config, _ = load_config(path)
        assert config.mode == "dry_run"
        assert config.dry_run is True
    finally:
        path.unlink(missing_ok=True)


def test_mode_paper_does_not_set_demo_mode():
    """mode=paper must NOT set demo_mode=True so real Demo orders can be placed when dry_run=false."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump({"mode": "paper", "dry_run": False}, f)
        path = Path(f.name)
    try:
        config, _ = load_config(path)
        assert config.mode == "paper"
        assert config.dry_run is False
        assert getattr(config, "demo_mode", False) is False
    finally:
        path.unlink(missing_ok=True)


def test_mode_paper_dry_run_true_simulated():
    """mode=paper + dry_run: true => simulated only."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump({"mode": "paper", "dry_run": True}, f)
        path = Path(f.name)
    try:
        config, _ = load_config(path)
        assert config.dry_run is True
    finally:
        path.unlink(missing_ok=True)


def test_mode_paper_dry_run_false_real_orders():
    """mode=paper + dry_run: false => real orders on selected env (Demo/Live)."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump({"mode": "paper", "dry_run": False}, f)
        path = Path(f.name)
    try:
        config, _ = load_config(path)
        assert config.dry_run is False
        assert config.mode == "paper"
    finally:
        path.unlink(missing_ok=True)


def test_execution_semantics_dry_run_controls_simulate():
    """dry_run is the single flag that controls simulate vs real orders (no demo_mode override)."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump({"mode": "paper", "dry_run": False}, f)
        path = Path(f.name)
    try:
        config, _ = load_config(path)
        will_simulate = config.dry_run
        will_place_real = not config.dry_run
        assert will_place_real is True
        assert will_simulate is False
    finally:
        path.unlink(missing_ok=True)
