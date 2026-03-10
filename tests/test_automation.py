import json
from pathlib import Path
from typing import Any

from src.automation.orchestrator import get_automation_status, run_demo_automation_cycle
from src.config.config import AutomationConfig, BurnInConfig, Config, EnvSettings
from src.storage.db import Database
from src.validation.readiness import READINESS_NOT_READY


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
        return cfg, EnvSettings()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)

    status = get_automation_status(config_path=Path("dummy.yaml"))
    assert "snapshot" in status
    assert isinstance(status["snapshot"], dict)


def test_automation_runs_only_in_demo(tmp_path: Path, monkeypatch) -> None:
    """Automation should stay IDLE when environment is not demo burn-in."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(enabled=True, demo_orchestration_enabled=True)
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="demo")

    env = EnvSettings()
    # Force non-demo environment
    env.bybit_env = "live"

    def _fake_load_config(_path):
        return cfg, env

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    # Not in demo burn-in -> automation remains IDLE
    assert snap["state"] == "IDLE"


def test_automation_waits_when_no_trades(tmp_path: Path, monkeypatch) -> None:
    """Automation moves to WAITING_FOR_BURNIN_DATA when readiness has no trades."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(enabled=True, demo_orchestration_enabled=True)
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="demo")

    env = EnvSettings()
    env.bybit_env = "demo"

    def _fake_load_config(_path):
        return cfg, env

    class DummyReadiness:
        def __init__(self) -> None:
            self.classification = READINESS_NOT_READY
            self.details: dict[str, Any] = {
                "trade_count": 0,
                "kill_switch_count": 0,
                "burnin_gate_breach_count": 0,
            }

    def _fake_compute_readiness(db, *, heartbeat_path, config_id, window_hours, burn_in_phase):
        return DummyReadiness()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "WAITING_FOR_BURNIN_DATA"
    assert snap["last_recommendation_status"] == "CONTINUE_DEMO"


def test_automation_blocked_by_kill_switch(tmp_path: Path, monkeypatch) -> None:
    """Kill switch in window moves automation to BLOCKED_BY_KILL_SWITCH."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        pause_on_kill_switch=True,
    )
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="demo")

    env = EnvSettings()
    env.bybit_env = "demo"

    def _fake_load_config(_path):
        return cfg, env

    class DummyReadiness:
        def __init__(self) -> None:
            self.classification = READINESS_NOT_READY
            self.details: dict[str, Any] = {
                "trade_count": 10,
                "kill_switch_count": 1,
                "burnin_gate_breach_count": 0,
            }

    def _fake_compute_readiness(db, *, heartbeat_path, config_id, window_hours, burn_in_phase):
        return DummyReadiness()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "BLOCKED_BY_KILL_SWITCH"
    assert snap["last_recommendation_status"] == "NOT_READY"


def test_automation_evaluates_and_optimizes_when_ready(tmp_path: Path, monkeypatch) -> None:
    """When trades and readiness are sufficient, automation runs evaluation, optimizer, shadow, and writes recommendation."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        min_trades_for_auto_evaluation=5,
        min_hours_between_evaluations=0.5,
        min_hours_between_optimizer_runs=1.0,
    )
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="demo")

    env = EnvSettings()
    env.bybit_env = "demo"

    def _fake_load_config(_path):
        return cfg, env

    class DummyReadiness:
        def __init__(self) -> None:
            self.classification = "READY_FOR_TESTNET_CONTINUATION"
            self.details: dict[str, Any] = {
                "trade_count": 10,
                "kill_switch_count": 0,
                "burnin_gate_breach_count": 0,
            }

    def _fake_compute_readiness(db, *, heartbeat_path, config_id, window_hours, burn_in_phase):
        return DummyReadiness()

    eval_called: dict[str, Any] = {}
    opt_called: dict[str, Any] = {}
    shadow_started: dict[str, Any] = {}

    class FakeEvaluator:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

        def run(self, from_ts=None, to_ts=None, config_id=None, symbol=None):
            eval_called["args"] = {
                "from_ts": from_ts,
                "to_ts": to_ts,
                "config_id": config_id,
            }
            return {"run_id": "eval123", "trade_count": 10, "report_path": "artifacts/evaluations/eval123.md"}

    def _fake_run_optimization(*, db_path: str, config_id: str, from_ts: int, to_ts: int, n_samples: int):
        opt_called["args"] = {
            "db_path": db_path,
            "config_id": config_id,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "n_samples": n_samples,
        }
        return {"run_id": "opt123", "best_candidate_config_id": "candidate_xyz"}

    class FakeShadowRunner:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

        def start(self, candidate_config_id: str) -> bool:
            shadow_started["candidate_config_id"] = candidate_config_id
            return True

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)
    monkeypatch.setattr("src.automation.orchestrator.Evaluator", FakeEvaluator)
    monkeypatch.setattr("src.automation.orchestrator.run_optimization", _fake_run_optimization)
    monkeypatch.setattr("src.automation.orchestrator.ShadowRunner", FakeShadowRunner)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]

    # Evaluation and optimizer should have been called
    assert snap["last_evaluation_run_id"] == "eval123"
    assert snap["last_optimizer_run_id"] == "opt123"
    assert snap["best_candidate_config_id"] == "candidate_xyz"
    assert snap["shadow_candidate_config_id"] == "candidate_xyz"
    assert "candidate_xyz" == shadow_started.get("candidate_config_id")

    # Recommendation artifact should exist
    auto_dir = Path("artifacts/automation")
    json_path = auto_dir / "automation_status.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data.get("snapshot", {}).get("best_candidate_config_id") == "candidate_xyz"

