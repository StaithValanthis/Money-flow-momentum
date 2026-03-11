"""Lightweight checks for install/run guide: script paths and key CLI commands. No exchange."""

from pathlib import Path

import pytest


def test_canonical_guide_exists():
    """docs/INSTALL_AND_RUN_GUIDE.md exists and mentions key commands."""
    root = Path(__file__).resolve().parents[1]
    guide = root / "docs" / "INSTALL_AND_RUN_GUIDE.md"
    assert guide.exists()
    text = guide.read_text(encoding="utf-8")
    assert "install.sh" in text
    assert "python run_bot.py validate" in text
    assert "show-runtime-mode" in text
    assert "promote-env" in text
    assert "start_testnet_burnin.sh" in text
    assert "check_burnin.sh" in text
    assert "start_small_live.sh" in text
    assert "config rollback" in text or "run_bot.py rollback" in text
    assert "incident_stop" in text
    assert "money-flow-momentum-automation.timer" in text
    assert "automation cycle" in text


def test_install_sh_exists():
    """install.sh exists at repo root."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "install.sh").exists()


def test_key_scripts_exist():
    """Key operator scripts from the guide exist."""
    root = Path(__file__).resolve().parents[1]
    scripts = [
        "scripts/validate_env.sh",
        "scripts/show_runtime_mode.sh",
        "scripts/start_testnet_burnin.sh",
        "scripts/check_burnin.sh",
        "scripts/promote_demo_to_live.sh",
        "scripts/check_small_live_ready.sh",
        "scripts/start_small_live.sh",
        "scripts/incident_stop.sh",
        "scripts/install_systemd.sh",
        "scripts/service_status.sh",
        "scripts/tail_logs.sh",
        "scripts/automation_status.sh",
    ]
    for s in scripts:
        assert (root / s).exists(), f"Missing {s}"


def test_config_example_and_env_example_exist():
    """config/config.yaml.example and .env.example exist."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "config.yaml.example").exists()
    assert (root / ".env.example").exists()


def test_systemd_service_file_exists():
    """money-flow-momentum.service exists at repo root."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "money-flow-momentum.service").exists()


def test_automation_systemd_units_exist():
    """Automation service and timer unit files exist at repo root."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "money-flow-momentum-automation.service").exists()
    assert (root / "money-flow-momentum-automation.timer").exists()


def test_automation_units_referenced_in_scripts():
    """Helper scripts reference the automation service/timer unit names correctly."""
    root = Path(__file__).resolve().parents[1]
    service_name = "money-flow-momentum-automation.service"
    timer_name = "money-flow-momentum-automation.timer"
    install = (root / "scripts" / "install_systemd.sh").read_text(encoding="utf-8")
    status = (root / "scripts" / "service_status.sh").read_text(encoding="utf-8")
    tail = (root / "scripts" / "tail_logs.sh").read_text(encoding="utf-8")
    assert service_name in install and timer_name in install
    assert service_name in status and timer_name in status
    assert service_name in tail


def test_dual_instance_install_ensures_log_dirs():
    """install_systemd.sh --dual-instance ensures logs/demo and logs/live exist before start."""
    root = Path(__file__).resolve().parents[1]
    install = (root / "scripts" / "install_systemd.sh").read_text(encoding="utf-8")
    assert "_ensure_dual_instance_dirs" in install
    assert "logs/demo" in install
    assert "logs/live" in install
    assert "mkdir -p" in install
