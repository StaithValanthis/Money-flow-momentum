import json
from pathlib import Path
from typing import Any

import pytest
from src.automation.orchestrator import get_automation_status, run_demo_automation_cycle
from src.config.config import AutomationConfig, BurnInConfig, Config, EnvSettings
from src.storage.db import Database
from src.storage.reconciliation import ReconciliationStore
from src.validation.readiness import READINESS_NOT_READY, READINESS_NEEDS_REVIEW


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


def test_automation_blocked_by_burnin_gate_breach(tmp_path: Path, monkeypatch) -> None:
    """Burn-in gate breach with trades => BLOCKED_BY_BURNIN, not WAITING_FOR_BURNIN_DATA."""
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
        classification = READINESS_NEEDS_REVIEW
        message = "Burn-in gate breach(es) in window"
        details = {"trade_count": 18, "kill_switch_count": 0, "burnin_gate_breach_count": 5}

    def _fake_compute_readiness(*args, **kwargs):
        return DummyReadiness()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "BLOCKED_BY_BURNIN"
    assert snap["last_recommendation_status"] == "NOT_READY"
    assert out["details"].get("reason") == "burnin_gate_breach"


def test_automation_trade_count_zero_stays_waiting_for_burnin_data(tmp_path: Path, monkeypatch) -> None:
    """trade_count == 0 => WAITING_FOR_BURNIN_DATA with CONTINUE_DEMO."""
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
        classification = "READY_FOR_TESTNET_CONTINUATION"
        message = "OK"
        details = {"trade_count": 0, "kill_switch_count": 0, "burnin_gate_breach_count": 0}

    def _fake_compute_readiness(*args, **kwargs):
        return DummyReadiness()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "WAITING_FOR_BURNIN_DATA"
    assert snap["last_recommendation_status"] == "CONTINUE_DEMO"


def test_automation_trade_count_positive_no_candidate_stays_ready_for_eval(tmp_path: Path, monkeypatch) -> None:
    """trade_count > 0, no candidate, no blocked conditions => READY_FOR_EVALUATION (not WAITING_FOR_BURNIN_DATA)."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    db.close()

    cfg = Config()
    cfg.database_path = str(db_path)
    cfg.automation = AutomationConfig(
        enabled=True,
        demo_orchestration_enabled=True,
        min_trades_for_auto_evaluation=100,
        min_hours_between_evaluations=24.0,
    )
    cfg.burn_in = BurnInConfig(burn_in_enabled=True, burn_in_phase="demo")
    env = EnvSettings()
    env.bybit_env = "demo"

    def _fake_load_config(_path):
        return cfg, env

    class DummyReadiness:
        classification = "READY_FOR_TESTNET_CONTINUATION"
        message = "OK"
        details = {"trade_count": 18, "kill_switch_count": 0, "burnin_gate_breach_count": 0}

    def _fake_compute_readiness(*args, **kwargs):
        return DummyReadiness()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "READY_FOR_EVALUATION"
    assert snap["state"] != "WAITING_FOR_BURNIN_DATA"
    assert snap["last_recommendation_status"] == "CONTINUE_DEMO"


def test_automation_blocked_by_health_when_not_ready_with_trades(tmp_path: Path, monkeypatch) -> None:
    """trade_count > 0 but readiness NOT_READY (e.g. other reason) => BLOCKED_BY_HEALTH."""
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
        classification = READINESS_NOT_READY
        message = "Protection mismatch(es) in window"
        details = {"trade_count": 10, "kill_switch_count": 0, "burnin_gate_breach_count": 0, "protection_mismatch_count": 1}

    def _fake_compute_readiness(*args, **kwargs):
        return DummyReadiness()

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.compute_readiness", _fake_compute_readiness)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    assert snap["state"] == "BLOCKED_BY_HEALTH"
    assert snap["last_recommendation_status"] == "NOT_READY"
    assert out["details"].get("reason") == "readiness_not_ok"


def test_automation_full_cycle_eval_opt_shadow_recommendation(tmp_path: Path, monkeypatch) -> None:
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


def test_readiness_and_evaluator_use_trades_table(tmp_path: Path) -> None:
    """Readiness trade_count and evaluator use the trades table; execution_audit alone is not enough."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))

    # Simulate what the bot now does on fill: insert_trade (and insert_fill/execution_audit are separate)
    now_ms = int(__import__("time").time() * 1000)
    # Place trades well inside the 24h window to avoid boundary issues
    window_start = now_ms - 24 * 3600 * 1000
    for i in range(5):
        db.insert_trade(
            ts=window_start + (i + 1) * 3600 * 1000,
            symbol="BTCUSDT",
            side="Buy",
            qty=0.01,
            price=50000.0,
            order_id=f"entry_oid_{i}",
            order_link_id=f"entry_{i}",
            pnl=0.0,
            config_id=None,
        )
    db.close()

    from src.validation.readiness import compute_readiness

    db2 = Database(str(db_path))
    result = compute_readiness(db2, config_id=None, window_hours=24.0, burn_in_phase="demo")
    db2.close()
    assert result.details.get("trade_count") == 5

    from src.evaluation.evaluator import Evaluator

    ev = Evaluator(str(db_path))
    from_ts = now_ms - 25 * 3600 * 1000
    to_ts = now_ms + 1000
    summary = ev.run(from_ts=from_ts, to_ts=to_ts, config_id=None)
    assert summary["trade_count"] == 5


def test_automation_progresses_past_waiting_when_trades_in_db(tmp_path: Path, monkeypatch) -> None:
    """When DB has trades (as after Demo fills with insert_trade), automation can progress past WAITING_FOR_BURNIN_DATA."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    now_ms = int(__import__("time").time() * 1000)
    for i in range(10):
        db.insert_trade(
            ts=now_ms - 12 * 3600 * 1000 + i * 3600 * 1000,
            symbol="BTCUSDT",
            side="Buy",
            qty=0.01,
            price=50000.0,
            order_id=f"oid_{i}",
            pnl=0.0,
            config_id=None,
        )
    db.close()

    from src.automation.orchestrator import run_demo_automation_cycle
    from src.config.config import AutomationConfig, BurnInConfig, Config, EnvSettings

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

    class FakeEvaluator:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

        def run(self, from_ts=None, to_ts=None, config_id=None, symbol=None, **kwargs):
            return {"run_id": "eval_ok", "trade_count": 10, "report_path": str(tmp_path / "eval.md")}

    def _fake_run_optimization(*, db_path: str, config_id: str, from_ts: int, to_ts: int, n_samples: int, **kwargs):
        return {"run_id": "opt_ok", "best_candidate_config_id": "cand_xyz"}

    class FakeShadowRunner:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path
        def start(self, candidate_config_id: str) -> bool:
            return True

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.Evaluator", FakeEvaluator)
    monkeypatch.setattr("src.automation.orchestrator.run_optimization", _fake_run_optimization)
    monkeypatch.setattr("src.automation.orchestrator.ShadowRunner", FakeShadowRunner)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    # Real compute_readiness sees trade_count=10 from DB; automation progresses past WAITING_FOR_BURNIN_DATA
    assert snap["state"] != "WAITING_FOR_BURNIN_DATA"
    assert snap.get("last_evaluation_run_id") == "eval_ok"


def test_automation_continue_demo_no_candidate_when_eval_and_opt_ran(tmp_path: Path, monkeypatch) -> None:
    """When evaluation and optimizer have run but no candidate met thresholds, state is CONTINUE_DEMO_NO_CANDIDATE."""
    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    now_ms = int(__import__("time").time() * 1000)
    for i in range(10):
        db.insert_trade(
            ts=now_ms - 12 * 3600 * 1000 + i * 3600 * 1000,
            symbol="BTCUSDT",
            side="Buy",
            qty=0.01,
            price=50000.0,
            order_id=f"oid_{i}",
            pnl=0.0,
            config_id=None,
        )
    db.close()

    from src.automation.orchestrator import run_demo_automation_cycle
    from src.automation.state import STATE_CONTINUE_DEMO_NO_CANDIDATE
    from src.config.config import AutomationConfig, BurnInConfig, Config, EnvSettings

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

    class FakeEvaluator:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

        def run(self, from_ts=None, to_ts=None, config_id=None, symbol=None, **kwargs):
            return {"run_id": "eval_no_cand", "trade_count": 10, "report_path": str(tmp_path / "eval.md")}

    def _fake_run_optimization(*, db_path: str, config_id: str, from_ts: int, to_ts: int, n_samples: int, **kwargs):
        return {"run_id": "opt_no_cand", "best_candidate_config_id": None}

    monkeypatch.setattr("src.automation.orchestrator.load_config", _fake_load_config)
    monkeypatch.setattr("src.automation.orchestrator.Evaluator", FakeEvaluator)
    monkeypatch.setattr("src.automation.orchestrator.run_optimization", _fake_run_optimization)

    out = run_demo_automation_cycle(config_path=Path("dummy.yaml"))
    snap = out["snapshot"]
    details = out["details"]
    assert snap["state"] == STATE_CONTINUE_DEMO_NO_CANDIDATE
    assert snap["last_evaluation_run_id"] == "eval_no_cand"
    assert snap["last_optimizer_run_id"] == "opt_no_cand"
    assert snap.get("best_candidate_config_id") is None
    assert "recommendation_message" in details
    assert "no candidate met thresholds" in details["recommendation_message"]
    assert "Continue Demo" in details["recommendation_message"]


def test_on_execution_persists_trade_when_order_not_in_recon(tmp_path: Path) -> None:
    """Execution callback writes to trades even when order is not yet in recon (e.g. execution before order update)."""
    from src.main import TradingBot
    from src.config.config import Config, EnvSettings

    db_path = tmp_path / "bot.db"
    cfg = Config()
    cfg.database_path = str(db_path)
    env = EnvSettings()
    bot = TradingBot(cfg, env)
    bot._db = Database(str(db_path))
    bot._recon = ReconciliationStore()
    bot._config_id = None

    execution_payload = {
        "orderId": "exec-order-123",
        "orderLinkId": "entry_abc",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execQty": "0.01",
        "execPrice": "50000",
        "execTime": str(int(__import__("time").time() * 1000)),
        "execId": "exec-id-456",
        "closedPnl": "0",
    }
    bot._on_execution(execution_payload)

    db2 = Database(str(db_path))
    trades = db2.get_trades()
    db2.close()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "BTCUSDT"
    assert trades[0]["side"] == "Buy"
    assert float(trades[0]["qty"]) == 0.01
    assert trades[0]["order_id"] == "exec-order-123"


def test_on_execution_persists_trade_when_entry_order_in_recon(tmp_path: Path) -> None:
    """Execution callback writes to trades when order is in recon with entry link."""
    from src.main import TradingBot
    from src.config.config import Config, EnvSettings
    from src.storage.reconciliation import OrderRecord

    db_path = tmp_path / "bot.db"
    cfg = Config()
    cfg.database_path = str(db_path)
    env = EnvSettings()
    bot = TradingBot(cfg, env)
    bot._db = Database(str(db_path))
    bot._recon = ReconciliationStore()
    bot._config_id = None

    order_id = "order-789"
    bot._recon.orders[order_id] = OrderRecord(
        order_id=order_id,
        order_link_id="entry_flow_123",
        symbol="ETHUSDT",
        side="Sell",
        qty=0.02,
        price=3000.0,
        order_type="Market",
        reduce_only=False,
        status="Filled",
        created_ts=0,
        updated_ts=0,
    )
    execution_payload = {
        "orderId": order_id,
        "symbol": "ETHUSDT",
        "side": "Sell",
        "execQty": "0.02",
        "execPrice": "2998.5",
        "execTime": str(int(__import__("time").time() * 1000)),
        "execId": "exec-xyz",
        "closedPnl": "0",
    }
    bot._on_execution(execution_payload)

    db2 = Database(str(db_path))
    trades = db2.get_trades()
    db2.close()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "ETHUSDT"
    assert trades[0]["side"] == "Sell"
    assert float(trades[0]["qty"]) == 0.02


def test_on_execution_stores_realized_pnl_from_execPnl(tmp_path: Path) -> None:
    """Bybit V5 sends realized PnL in execPnl; trade and fill must store it (not only closedPnl)."""
    from src.main import TradingBot
    from src.config.config import Config, EnvSettings
    from src.storage.db import Database

    db_path = tmp_path / "bot.db"
    cfg = Config()
    cfg.database_path = str(db_path)
    env = EnvSettings()
    bot = TradingBot(cfg, env)
    bot._db = Database(str(db_path))
    bot._recon = ReconciliationStore()
    bot._config_id = None

    execution_payload = {
        "orderId": "pnl-order-1",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "execQty": "0.1",
        "execPrice": "96500",
        "execTime": str(int(__import__("time").time() * 1000)),
        "execId": "exec-pnl-1",
        "closedPnl": "0",
        "execPnl": "12.50",
    }
    bot._on_execution(execution_payload)

    db2 = Database(str(db_path))
    trades = db2.get_trades()
    fills = db2.get_fills()
    db2.close()
    assert len(trades) == 1
    assert float(trades[0]["pnl"]) == 12.5
    assert len(fills) == 1
    assert float(fills[0]["closed_pnl"]) == 12.5


def test_evaluator_fallback_from_fills_when_trades_pnl_zero(tmp_path: Path) -> None:
    """When trades have pnl=0 (e.g. old DB), evaluator uses fills.closed_pnl for total_pnl."""
    from src.storage.db import Database
    from src.evaluation.evaluator import Evaluator

    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    ts = 1000000
    db.insert_trade(ts, "BTCUSDT", "Sell", 0.01, 50000.0, order_id="o1", pnl=0.0)
    db.insert_trade(ts + 1000, "ETHUSDT", "Buy", 0.1, 3000.0, order_id="o2", pnl=0.0)
    db.insert_fill(ts, "e1", "o1", "BTCUSDT", "Sell", 0.01, 50000.0, closed_pnl=10.0)
    db.insert_fill(ts + 1000, "e2", "o2", "ETHUSDT", "Buy", 0.1, 3000.0, closed_pnl=-3.0)
    db.close()

    ev = Evaluator(str(db_path))
    summary = ev.run(from_ts=ts - 1, to_ts=ts + 2000, initial_equity=10_000.0)
    core = summary["core"]
    assert core["total_pnl"] == 7.0
    assert core["return_pct"] == pytest.approx(0.07, rel=1e-2)
    assert core["win_rate"] == 0.5
    assert core["expectancy"] == 3.5


def test_evaluator_pairing_long_entry_exit_nonzero_pnl(tmp_path: Path) -> None:
    """Evaluator computes realized PnL from entry+exit pairing when trades/fills have no PnL."""
    from src.storage.db import Database
    from src.evaluation.evaluator import Evaluator

    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    ts = 1000000
    db.insert_trade(ts, "BTCUSDT", "Buy", 0.01, 50_000.0, order_id="entry-o", order_link_id="entry_abc", pnl=0.0)
    db.insert_trade(ts + 5000, "BTCUSDT", "Sell", 0.01, 51_000.0, order_id="tp-o", order_link_id="tp1_xyz", pnl=0.0)
    db.close()

    ev = Evaluator(str(db_path))
    summary = ev.run(from_ts=ts - 1, to_ts=ts + 10_000, initial_equity=10_000.0)
    assert summary["core"]["total_pnl"] == pytest.approx(10.0)
    assert summary["core"]["return_pct"] == pytest.approx(0.1, rel=1e-2)


def test_evaluator_pairing_short_entry_exit_nonzero_pnl(tmp_path: Path) -> None:
    """Short entry + exit => evaluator reports correct realized PnL via pairing."""
    from src.storage.db import Database
    from src.evaluation.evaluator import Evaluator

    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    ts = 2000000
    db.insert_trade(ts, "ETHUSDT", "Sell", 0.1, 3_000.0, order_id="entry-o", order_link_id="entry_def", pnl=0.0)
    db.insert_trade(ts + 3000, "ETHUSDT", "Buy", 0.1, 2_900.0, order_id="tp-o", order_link_id="tp1_uvw", pnl=0.0)
    db.close()

    ev = Evaluator(str(db_path))
    summary = ev.run(from_ts=ts - 1, to_ts=ts + 10_000, initial_equity=10_000.0)
    assert summary["core"]["total_pnl"] == pytest.approx(10.0)


def test_evaluator_pairing_partial_tp_nonzero_pnl(tmp_path: Path) -> None:
    """Partial TP1 + TP2 exits => evaluator total_pnl from pairing."""
    from src.storage.db import Database
    from src.evaluation.evaluator import Evaluator

    db_path = tmp_path / "bot.db"
    db = Database(str(db_path))
    ts = 3000000
    db.insert_trade(ts, "BTCUSDT", "Buy", 0.03, 50_000.0, order_id="e1", order_link_id="entry_1", pnl=0.0)
    db.insert_trade(ts + 1000, "BTCUSDT", "Sell", 0.01, 51_000.0, order_id="tp1", order_link_id="tp1_a", pnl=0.0)
    db.insert_trade(ts + 2000, "BTCUSDT", "Sell", 0.02, 52_000.0, order_id="tp2", order_link_id="tp2_b", pnl=0.0)
    db.close()

    ev = Evaluator(str(db_path))
    summary = ev.run(from_ts=ts - 1, to_ts=ts + 10_000, initial_equity=10_000.0)
    assert summary["core"]["total_pnl"] == pytest.approx(50.0)  # 10 + 40

