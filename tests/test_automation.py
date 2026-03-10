import json
from pathlib import Path

from src.automation.orchestrator import get_automation_status, run_demo_automation_cycle
from src.config.config import Config, AutomationConfig, BurnInConfig
from src.storage.db import Database


def test_automation_idle_when_disabled(tmp_path: Path, monkeypatch) -> None:
    """Automation should stay IDLE when disabled in config."""

    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(enabled=False, demo_orchestration_enabled=False)
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="demo")

    def _fake_load_config(_path):
        from src.config.config import EnvSettings

        return cfg, EnvSettings()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "IDLE"


def test_automation_status_no_artifact(tmp_path: Path, monkeypatch) -> None:
    """automation status should work even when no artifact exists yet."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(enabled=True, demo_orchestration_enabled=True)
    cfg.burn_in = BurnInConfig(burn_in_enabled=False)

    def _fake_load_config(_path):
        from src.config.config import EnvSettings

        return cfg, EnvSettings()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)

    status = get_automation_status(config_path=Path("dummy.yaml"))
    assert "snapshot" in status
    assert isinstance(status["snapshot"], dict)

