"""Tests for Demo-only auto-adopt: candidate becomes Demo active config; Live unchanged."""

from pathlib import Path

import pytest

from src.automation.orchestrator import run_demo_automation_cycle
from src.config.config import AutomationConfig, BurnInConfig, Config, EnvSettings
from src.storage.db import Database


def test_demo_auto_adopt_when_rules_satisfied(tmp_path: Path, monkeypatch) -> None:
    """Demo with auto_adopt_demo_candidates=True and rules satisfied: candidate becomes Demo active, event recorded."""
    from src.config.versioning import (
        ensure_stage3_schema,
        register_config_version,
        get_active_config_id,
        activate_config_version,
    )

    db_path = tmp_path / "bot.db"
    Database(str(db_path)).close()
    ensure_stage3_schema(str(db_path))
    artifact_dir = tmp_path / "artifacts" / "configs"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    cfg_a = Config()
    cfg_a.database_path = str(db_path)
    cfg_b = Config()
    cfg_b.entry.long_threshold = 1.5
    cfg_b.database_path = str(db_path)

    id_a = register_config_version(
        cfg_a, "v1", "active", "active baseline", db_path=str(db_path), artifact_dir=artifact_dir
    )
    id_b = register_config_version(
        cfg_b, "v2", "candidate", "candidate from optimizer", source="optimizer", db_path=str(db_path), artifact_dir=artifact_dir
    )
    activate_config_version(id_a, str(db_path), reason="bootstrap", manual=True)

    assert get_active_config_id(str(db_path)) == id_a

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.artifacts_root = str(tmp_path / "artifacts")
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        auto_adopt_demo_candidates=True,
        min_trades_for_demo_adoption=10,
        min_hours_between_demo_adoptions=1.0,
        require_shadow_before_demo_adoption=False,
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
        classification = "READY_FOR_TESTNET_CONTINUATION"
        message = "OK"
        details = {"trade_count": 10, "kill_switch_count": 0, "burnin_gate_breach_count": 0}

    def _fake_run_optimization(*, db_path, config_id, from_ts, to_ts, n_samples, **kwargs):
        return {"run_id": "opt_demo", "best_candidate_config_id": id_b}

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", lambda *a, **k: DummyReadiness())
    monkeypatch.setattr("src.automation.orchestrator.Evaluator", lambda db_path: type("FakeEv", (), {"run": lambda *a, **k: {"run_id": "e1"}})())
    monkeypatch.setattr("src.automation.orchestrator.run_optimization", _fake_run_optimization)
    monkeypatch.setattr("src.automation.orchestrator.ShadowRunner", lambda db_path: type("FakeShadow", (), {"start": lambda self, cid: True})())

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    details = out["details"]

    assert snap["state"] == "DEMO_AUTO_ADOPTED"
    assert snap["last_recommendation_status"] == "DEMO_AUTO_ADOPTED"
    assert snap["best_candidate_config_id"] == id_b
    assert snap.get("last_demo_adoption_ts") is not None
    assert details.get("reason") == "demo_auto_adopted"
    assert details.get("adopted_config_id") == id_b
    assert get_active_config_id(str(db_path)) == id_b

    conn = __import__("sqlite3").connect(str(db_path))
    row = conn.execute(
        "SELECT promoted_config_id, previous_active_config_id, reason, manual FROM promotion_events ORDER BY promoted_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == id_b
    assert row[1] == id_a
    assert row[2] == "demo_auto_adopt"
    assert row[3] == 0


def test_demo_auto_adopt_not_done_when_disabled(tmp_path: Path, monkeypatch) -> None:
    """When auto_adopt_demo_candidates is False, no auto-adopt; state stays AWAITING_MANUAL_APPROVAL."""
    from src.config.versioning import ensure_stage3_schema, register_config_version, get_active_config_id, activate_config_version

    db_path = tmp_path / "bot.db"
    Database(str(db_path)).close()
    ensure_stage3_schema(str(db_path))
    artifact_dir = tmp_path / "artifacts" / "configs"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    cfg_a = Config()
    cfg_a.database_path = str(db_path)
    id_a = register_config_version(cfg_a, "v1", "active", "active", db_path=str(db_path), artifact_dir=artifact_dir)
    cfg_b = Config()
    cfg_b.entry.long_threshold = 1.5
    id_b = register_config_version(cfg_b, "v2", "candidate", "candidate", source="optimizer", db_path=str(db_path), artifact_dir=artifact_dir)
    activate_config_version(id_a, str(db_path), reason="bootstrap", manual=True)

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.artifacts_root = str(tmp_path / "artifacts")
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        auto_adopt_demo_candidates=False,
        min_trades_for_demo_adoption=10,
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
        classification = "READY_FOR_TESTNET_CONTINUATION"
        message = "OK"
        details = {"trade_count": 10, "kill_switch_count": 0, "burnin_gate_breach_count": 0}

    def _fake_run_optimization(*, db_path, config_id, from_ts, to_ts, n_samples, **kwargs):
        return {"run_id": "opt1", "best_candidate_config_id": id_b}

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", lambda *a, **k: DummyReadiness())
    monkeypatch.setattr("src.automation.orchestrator.Evaluator", lambda db_path: type("FakeEv", (), {"run": lambda *a, **k: {"run_id": "e1"}})())
    monkeypatch.setattr("src.automation.orchestrator.run_optimization", _fake_run_optimization)
    monkeypatch.setattr("src.automation.orchestrator.ShadowRunner", lambda db_path: type("FakeShadow", (), {"start": lambda self, cid: True})())

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "AWAITING_MANUAL_APPROVAL"
    assert snap["last_recommendation_status"] == "READY_FOR_CONFIG_REVIEW"
    assert get_active_config_id(str(db_path)) == id_a


def test_demo_auto_adopt_cooldown_enforced(tmp_path: Path, monkeypatch) -> None:
    """When last_demo_adoption_ts is recent, no second adoption."""
    from src.automation.state import STATE_DEMO_AUTO_ADOPTED
    from src.config.versioning import ensure_stage3_schema, register_config_version, get_active_config_id, activate_config_version

    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()
    ensure_stage3_schema(str(db_path))
    artifact_dir = tmp_path / "artifacts" / "configs"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    cfg_a = Config()
    cfg_a.database_path = str(db_path)
    id_a = register_config_version(cfg_a, "v1", "active", "active", db_path=str(db_path), artifact_dir=artifact_dir)
    cfg_b = Config()
    cfg_b.entry.long_threshold = 1.5
    id_b = register_config_version(cfg_b, "v2", "candidate", "candidate", source="optimizer", db_path=str(db_path), artifact_dir=artifact_dir)
    activate_config_version(id_a, str(db_path), reason="bootstrap", manual=True)

    db2 = Database(str(db_path))
    now_ms = int(__import__("time").time() * 1000)
    snap_dict = {
        "state": STATE_DEMO_AUTO_ADOPTED,
        "last_demo_adoption_ts": now_ms - 1000,
        "best_candidate_config_id": id_b,
        "shadow_candidate_config_id": id_b,
        "last_optimizer_ts": now_ms,
        "last_evaluation_ts": now_ms,
        "updated_ts": now_ms,
    }
    db2.upsert_automation_state(snap_dict)
    db2.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.artifacts_root = str(tmp_path / "artifacts")
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        auto_adopt_demo_candidates=True,
        min_trades_for_demo_adoption=10,
        min_hours_between_demo_adoptions=24.0,
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
        classification = "READY_FOR_TESTNET_CONTINUATION"
        message = "OK"
        details = {"trade_count": 10, "kill_switch_count": 0, "burnin_gate_breach_count": 0}

    def _fake_run_optimization(*, db_path, config_id, from_ts, to_ts, n_samples, **kwargs):
        return {"run_id": "opt2", "best_candidate_config_id": id_b}

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", lambda *a, **k: DummyReadiness())
    monkeypatch.setattr("src.automation.orchestrator.Evaluator", lambda db_path: type("FakeEv", (), {"run": lambda *a, **k: {"run_id": "e2"}})())
    monkeypatch.setattr("src.automation.orchestrator.run_optimization", _fake_run_optimization)
    monkeypatch.setattr("src.automation.orchestrator.ShadowRunner", lambda db_path: type("FakeShadow", (), {"start": lambda self, cid: True})())

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    details = out["details"]
    assert get_active_config_id(str(db_path)) == id_a
    assert details.get("reason") != "demo_auto_adopted"


def test_live_never_auto_adopts(tmp_path: Path, monkeypatch) -> None:
    """When env is live (not demo), automation stays IDLE; active config unchanged."""
    from src.config.versioning import ensure_stage3_schema, register_config_version, get_active_config_id, activate_config_version

    db_path = tmp_path / "bot.db"
    Database(str(db_path)).close()
    ensure_stage3_schema(str(db_path))
    artifact_dir = tmp_path / "artifacts" / "configs"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    cfg_a = Config()
    cfg_a.database_path = str(db_path)
    id_a = register_config_version(cfg_a, "v1", "active", "active", db_path=str(db_path), artifact_dir=artifact_dir)
    cfg_b = Config()
    cfg_b.entry.long_threshold = 1.5
    id_b = register_config_version(cfg_b, "v2", "candidate", "candidate", source="optimizer", db_path=str(db_path), artifact_dir=artifact_dir)
    activate_config_version(id_a, str(db_path), reason="bootstrap", manual=True)

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.artifacts_root = str(tmp_path / "artifacts")
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        auto_adopt_demo_candidates=True,
        min_trades_for_demo_adoption=10,
        min_trades_for_auto_evaluation=5,
        min_hours_between_evaluations=0.5,
        min_hours_between_optimizer_runs=1.0,
    )
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="live_guarded")
    env = EnvSettings()
    env.bybit_env = "live"

    def _fake_load_config(_path):
        return cfg, env

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "IDLE"
    assert get_active_config_id(str(db_path)) == id_a
