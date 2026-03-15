"""Tests for master bot journal (single system-wide log for Demo + Live)."""

from pathlib import Path

import pytest

from src.journal.logger import (
    append_journal_event,
    get_journal_jsonl_path,
    get_journal_log_path,
    write_journal_line,
)
from src.journal.logger import _system_dir  # noqa: PLC2701


def test_journal_path_is_global_system() -> None:
    """Master journal lives under artifacts/system, not per-instance."""
    # artifacts/demo -> artifacts/system
    p = _system_dir("artifacts/demo")
    assert p == Path("artifacts/system")
    # artifacts/live -> artifacts/system
    p2 = _system_dir("artifacts/live")
    assert p2 == Path("artifacts/system")
    # artifacts -> artifacts/system
    p3 = _system_dir("artifacts")
    assert p3 == Path("artifacts/system")


def test_append_journal_writes_log_and_jsonl(tmp_path: Path) -> None:
    """append_journal_event writes to both human log and JSONL."""
    root = str(tmp_path / "artifacts")
    append_journal_event(root, "WARMUP", "started", instance="demo")
    append_journal_event(root, "DEMO_PROBATION", "failed", instance="demo", config_id="c1", reason="stalled", failure_reason_type="fail_fast_stalled_poor_metrics", metrics={"pf": 0.71})

    log_path = get_journal_log_path(root)
    jsonl_path = get_journal_jsonl_path(root)
    assert log_path.exists()
    assert jsonl_path.exists()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "WARMUP: started" in lines[0] and "instance=demo" in lines[0]
    assert "DEMO_PROBATION: failed" in lines[1] and "config_id=c1" in lines[1]

    import json
    jsonl_lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().splitlines() if l]
    assert len(jsonl_lines) == 2
    first = json.loads(jsonl_lines[0])
    assert first["phase"] == "WARMUP" and first["event"] == "started" and first.get("instance") == "demo"
    second = json.loads(jsonl_lines[1])
    assert second["phase"] == "DEMO_PROBATION" and second.get("failure_reason_type") == "fail_fast_stalled_poor_metrics"


def test_lifecycle_dual_writes_to_journal(tmp_path: Path) -> None:
    """Demo lifecycle events are also written to master journal."""
    from src.lifecycle.logger import append_demo_lifecycle_event
    root = str(tmp_path / "artifacts" / "demo")
    append_demo_lifecycle_event(root, "demo", "DEMO_INIT", "started")
    append_demo_lifecycle_event(root, "demo", "AUTO_REINIT", "reinit_requested")

    journal_path = get_journal_log_path(root)
    assert journal_path.exists()
    content = journal_path.read_text(encoding="utf-8")
    assert "DEMO_INIT: started" in content
    assert "AUTO_REINIT: reinit_requested" in content


def test_live_promotion_journal(tmp_path: Path) -> None:
    """Live promotion events write to master journal."""
    root = str(tmp_path / "artifacts" / "live")
    append_journal_event(root, "LIVE_PROMOTION", "started", instance="live", candidate_config_id="cfg-demo-1")
    append_journal_event(root, "LIVE_PROMOTION", "completed", instance="live", candidate_config_id="cfg-demo-1", config_id="cfg-live-1", status="activated")

    journal_path = get_journal_log_path(root)
    content = journal_path.read_text(encoding="utf-8")
    assert "LIVE_PROMOTION: started" in content and "candidate_config_id=cfg-demo-1" in content
    assert "LIVE_PROMOTION: completed" in content and "status=activated" in content


def test_candidate_ready_for_review_and_runtime_blocked(tmp_path: Path) -> None:
    """Candidate ready_for_review and RUNTIME blocked write to journal."""
    root = str(tmp_path / "artifacts" / "demo")
    append_journal_event(root, "CANDIDATE", "ready_for_review", instance="demo", candidate_config_id="c1")
    append_journal_event(root, "RUNTIME", "blocked", instance="demo", reason="kill_switch_in_window", status="BLOCKED_BY_KILL_SWITCH")

    journal_path = get_journal_log_path(root)
    content = journal_path.read_text(encoding="utf-8")
    assert "CANDIDATE: ready_for_review" in content
    assert "RUNTIME: blocked" in content and "kill_switch" in content


def test_write_journal_line(tmp_path: Path) -> None:
    """write_journal_line appends only to .log."""
    root = str(tmp_path / "artifacts")
    write_journal_line(root, "Custom operator note")
    log_path = get_journal_log_path(root)
    assert log_path.exists()
    assert "Custom operator note" in log_path.read_text(encoding="utf-8")
    jsonl_path = get_journal_jsonl_path(root)
    assert not jsonl_path.exists() or jsonl_path.read_text().strip() == ""
