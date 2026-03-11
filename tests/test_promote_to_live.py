"""Tests for cross-instance promote-to-live: import Demo candidate into Live."""

from pathlib import Path

import pytest

from src.config.config import Config
from src.config.versioning import (
    ensure_stage3_schema,
    get_active_config_id,
    list_config_versions,
    register_config_version,
    load_config_from_artifact,
    import_candidate_to_live,
)


def _minimal_config() -> Config:
    """Minimal valid Config for tests."""
    return Config(
        mode="paper",
        dry_run=True,
        database_path="data/bot.db",
        artifacts_root="artifacts",
        logs_dir="logs",
    )


def test_import_candidate_to_live_success(tmp_path):
    """Candidate in Demo is imported into Live successfully."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    demo_art = tmp_path / "demo_art"
    demo_art.mkdir(parents=True)
    live_art = tmp_path / "live_art" / "configs"
    live_art.mkdir(parents=True)

    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)
    config = _minimal_config()
    candidate_id = register_config_version(
        config,
        version="cand1",
        status="candidate",
        description="Demo candidate",
        source="optimizer",
        db_path=demo_db,
        artifact_dir=demo_art,
    )
    result = import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
        activate=False,
        dry_run=False,
    )
    assert result["ok"] is True
    assert result["error"] is None
    assert result["imported"] is True
    assert result["already_present"] is False
    assert result["activated"] is False
    assert result["live_config_id"] is not None
    live_versions = list_config_versions(db_path=live_db)
    assert len(live_versions) == 1
    assert live_versions[0]["config_id"] == result["live_config_id"]
    assert live_versions[0]["source"] == "demo_import"
    assert live_versions[0]["status"] == "candidate"


def test_import_does_not_activate_by_default(tmp_path):
    """Default behavior does not activate in Live."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    demo_art = tmp_path / "demo_art"
    demo_art.mkdir(parents=True)
    live_art = tmp_path / "live_art" / "configs"
    live_art.mkdir(parents=True)

    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)
    candidate_id = register_config_version(
        _minimal_config(),
        version="c",
        status="candidate",
        db_path=demo_db,
        artifact_dir=demo_art,
    )
    result = import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
        activate=False,
    )
    assert result["ok"] is True
    assert result["activated"] is False
    assert get_active_config_id(live_db) is None


def test_import_with_activate_activates_in_live(tmp_path):
    """--activate flag activates the imported config in Live."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    demo_art = tmp_path / "demo_art"
    demo_art.mkdir(parents=True)
    live_art = tmp_path / "live_art" / "configs"
    live_art.mkdir(parents=True)

    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)
    candidate_id = register_config_version(
        _minimal_config(),
        version="c",
        status="candidate",
        db_path=demo_db,
        artifact_dir=demo_art,
    )
    result = import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
        activate=True,
    )
    assert result["ok"] is True
    assert result["activated"] is True
    assert get_active_config_id(live_db) == result["live_config_id"]


def test_import_missing_demo_candidate_fails(tmp_path):
    """Missing Demo candidate fails clearly."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)

    result = import_candidate_to_live(
        candidate_config_id="nonexistent_id_123",
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=tmp_path / "live_art" / "configs",
    )
    assert result["ok"] is False
    assert "not found" in (result["error"] or "").lower()
    assert result["live_config_id"] is None


def test_import_duplicate_uses_existing_and_reports_already_present(tmp_path):
    """Re-importing same content (same hash) reuses Live config and reports already_present."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    demo_art = tmp_path / "demo_art"
    demo_art.mkdir(parents=True)
    live_art = tmp_path / "live_art" / "configs"
    live_art.mkdir(parents=True)

    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)
    config = _minimal_config()
    candidate_id = register_config_version(
        config,
        version="c",
        status="candidate",
        db_path=demo_db,
        artifact_dir=demo_art,
    )
    result1 = import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
    )
    assert result1["ok"] is True
    assert result1["imported"] is True
    assert result1["already_present"] is False
    first_live_id = result1["live_config_id"]

    result2 = import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
    )
    assert result2["ok"] is True
    assert result2["already_present"] is True
    assert result2["live_config_id"] == first_live_id
    live_versions = list_config_versions(db_path=live_db)
    assert len(live_versions) == 1


def test_cross_instance_isolation(tmp_path):
    """Demo and Live DBs remain separate; Demo unchanged after import."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    demo_art = tmp_path / "demo_art"
    demo_art.mkdir(parents=True)
    live_art = tmp_path / "live_art" / "configs"
    live_art.mkdir(parents=True)

    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)
    candidate_id = register_config_version(
        _minimal_config(),
        version="c",
        status="candidate",
        db_path=demo_db,
        artifact_dir=demo_art,
    )
    demo_before = list_config_versions(db_path=demo_db)
    import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
    )
    demo_after = list_config_versions(db_path=demo_db)
    live_after = list_config_versions(db_path=live_db)
    assert len(demo_before) == 1
    assert len(demo_after) == 1
    assert demo_after[0]["config_id"] == candidate_id
    assert len(live_after) == 1
    assert live_after[0]["source"] == "demo_import"


def test_dry_run_does_not_write(tmp_path):
    """--dry-run reports success but does not write to Live."""
    demo_db = str(tmp_path / "demo" / "bot.db")
    live_db = str(tmp_path / "live" / "bot.db")
    (tmp_path / "demo").mkdir(parents=True)
    (tmp_path / "live").mkdir(parents=True)
    demo_art = tmp_path / "demo_art"
    demo_art.mkdir(parents=True)
    live_art = tmp_path / "live_art" / "configs"

    ensure_stage3_schema(demo_db)
    ensure_stage3_schema(live_db)
    candidate_id = register_config_version(
        _minimal_config(),
        version="c",
        status="candidate",
        db_path=demo_db,
        artifact_dir=demo_art,
    )
    result = import_candidate_to_live(
        candidate_config_id=candidate_id,
        demo_db_path=demo_db,
        live_db_path=live_db,
        live_artifact_dir=live_art,
        dry_run=True,
    )
    assert result["ok"] is True
    assert result.get("dry_run") is True
    live_versions = list_config_versions(db_path=live_db)
    assert len(live_versions) == 0


def test_load_config_from_artifact_still_works_after_refactor(tmp_path):
    """load_config_from_artifact (used by main) still loads config from artifact."""
    db_path = str(tmp_path / "bot.db")
    art_dir = tmp_path / "configs"
    art_dir.mkdir(parents=True)
    ensure_stage3_schema(db_path)
    config = _minimal_config()
    config_id = register_config_version(
        config,
        version="v1",
        status="active",
        db_path=db_path,
        artifact_dir=art_dir,
    )
    loaded = load_config_from_artifact(config_id, db_path)
    assert loaded is not None
    assert loaded.mode == config.mode
    assert loaded.dry_run == config.dry_run
