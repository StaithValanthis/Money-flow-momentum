"""Tests for post-burnin pipeline helper."""

from pathlib import Path

import json
import tempfile

import pytest

from src.cli.stage3_commands import run_post_burnin_pipeline


class DummyDB:
    """Minimal fake Database for pipeline tests."""

    def __init__(self, path: str):
        self.path = path

    def _get_conn(self):
        class Conn:
            def __init__(self):
                self._rows = [("cand1", "opt1", 1234567890)]

            def execute(self, sql, params=()):
                class Res:
                    def __init__(self, rows):
                        self._rows = rows

                    def fetchall(self):
                        return self._rows

                    def fetchone(self):
                        return self._rows[0] if self._rows else None

                return Res(self._rows)

        return Conn()

    def close(self):
        pass


class DummyEvaluator:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self, from_ts=None, to_ts=None, config_id=None, symbol=None):
        return {"run_id": "eval1", "report_path": "artifacts/evaluations/eval1.md", "trade_count": 10}


def test_post_burnin_stops_when_readiness_not_acceptable(monkeypatch, tmp_path):
    """Helper should stop after readiness when classification is NOT_READY."""

    from src.cli import stage3_commands as sc

    def fake_load_config(path=None):
        from src.config.config import Config, EnvSettings

        return Config(), EnvSettings()

    class FakeReadiness:
        def __init__(self):
            self.classification = "NOT_READY"
            self.message = "Kill switch triggered"
            self.details = {}

    def fake_compute_readiness(db, **kwargs):
        return FakeReadiness()

    # Patch load_config / Database / compute_readiness; let get_active_config_id
    # use its default behavior (not exercised here, since selected_config_id is None-OK).
    monkeypatch.setattr(sc, "load_config", fake_load_config)
    monkeypatch.setattr(sc, "Database", DummyDB)
    monkeypatch.setattr(sc, "compute_readiness", fake_compute_readiness)

    summary = run_post_burnin_pipeline(
        config_path=None,
        from_date=None,
        to_date=None,
        config_id=None,
        n_samples=5,
        window_hours=24.0,
        start_shadow=False,
        shadow_report=False,
        output_dir=tmp_path,
    )

    assert summary["readiness"]["classification"] == "NOT_READY"
    assert summary["readiness_acceptable"] is False
    assert summary["evaluation"] is None
    assert summary["optimizer"] is None
    assert summary["candidates"] is None
    assert summary["shadow"] is None


def test_post_burnin_runs_eval_and_optimizer_when_ready(monkeypatch, tmp_path):
    """Helper runs evaluation and optimizer when readiness is acceptable."""

    from src.cli import stage3_commands as sc

    def fake_load_config(path=None):
        from src.config.config import Config, EnvSettings

        return Config(), EnvSettings()

    class FakeReadiness:
        def __init__(self):
            self.classification = sc.READINESS_READY_SMALL_LIVE
            self.message = "OK for small live"
            self.details = {}

    def fake_compute_readiness(db, **kwargs):
        return FakeReadiness()

    def fake_run_optimization(db_path: str, config_id=None, from_ts=None, to_ts=None, n_samples: int = 20):
        return {"run_id": "opt1", "best_candidate_config_id": "cand1"}

    monkeypatch.setattr(sc, "load_config", fake_load_config)
    monkeypatch.setattr(sc, "Database", DummyDB)
    monkeypatch.setattr(sc, "compute_readiness", fake_compute_readiness)
    monkeypatch.setattr(sc, "Evaluator", DummyEvaluator)
    monkeypatch.setattr(sc, "run_optimization", fake_run_optimization)

    summary = run_post_burnin_pipeline(
        config_path=None,
        from_date=None,
        to_date=None,
        config_id=None,
        n_samples=5,
        window_hours=24.0,
        start_shadow=False,
        shadow_report=False,
        output_dir=tmp_path,
    )

    assert summary["readiness_acceptable"] is True
    assert summary["evaluation"]["run_id"] == "eval1"
    assert summary["optimizer"]["run_id"] == "opt1"
    assert summary["candidates"]["top_candidate_id"] == "cand1"
    # No promotion or env switch implied
    for cmd in summary.get("next_commands", []):
        assert "promote-env" not in cmd


def test_post_burnin_writes_summary_artifact(monkeypatch, tmp_path):
    """Helper writes a JSON summary when invoked via wrapper logic."""

    from src.cli import stage3_commands as sc

    def fake_load_config(path=None):
        from src.config.config import Config, EnvSettings

        return Config(), EnvSettings()

    class FakeReadiness:
        def __init__(self):
            self.classification = sc.READINESS_READY_TESTNET
            self.message = "OK for testnet continuation"
            self.details = {}

    def fake_compute_readiness(db, **kwargs):
        return FakeReadiness()

    def fake_run_optimization(db_path: str, config_id=None, from_ts=None, to_ts=None, n_samples: int = 20):
        return {"run_id": "opt2", "best_candidate_config_id": "cand1"}

    monkeypatch.setattr(sc, "load_config", fake_load_config)
    monkeypatch.setattr(sc, "Database", DummyDB)
    monkeypatch.setattr(sc, "compute_readiness", fake_compute_readiness)
    monkeypatch.setattr(sc, "Evaluator", DummyEvaluator)
    monkeypatch.setattr(sc, "run_optimization", fake_run_optimization)

    summary = run_post_burnin_pipeline(
        config_path=None,
        from_date=None,
        to_date=None,
        config_id=None,
        n_samples=5,
        window_hours=24.0,
        start_shadow=False,
        shadow_report=False,
        output_dir=tmp_path,
    )

    # Persist summary to a JSON in pipeline-like dir
    ts = summary["timestamp_ms"]
    json_path = tmp_path / f"post_burnin_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f)

    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["optimizer"]["run_id"] == "opt2"

