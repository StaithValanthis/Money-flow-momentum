"""Master bot journal: single append-only operator log for full lifecycle (Demo + Live)."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()


def _system_dir(artifacts_root: str) -> Path:
    """Return artifacts/system dir (global, not per-instance)."""
    base = Path(artifacts_root)
    # If artifacts_root is artifacts/demo or artifacts/live, use parent/system.
    if base.name in ("demo", "live", "main"):
        return base.parent / "system"
    return base / "system"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def append_journal_event(
    artifacts_root: str,
    phase: str,
    event: str,
    *,
    instance: Optional[str] = None,
    config_id: Optional[str] = None,
    candidate_config_id: Optional[str] = None,
    reason: Optional[str] = None,
    failure_reason_type: Optional[str] = None,
    status: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
    human_line: Optional[str] = None,
    write_jsonl: bool = True,
) -> Optional[Path]:
    """
    Append one event to the master bot journal (human log + optional JSONL).
    Safe to call from any phase/process (append-only, locked).
    """
    try:
        dir_path = _system_dir(artifacts_root)
        _ensure_dir(dir_path)
        log_path = dir_path / "bot_journal.log"
        jsonl_path = dir_path / "bot_journal.jsonl"

        ts = time.time()
        ts_ms = int(ts * 1000)
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        payload: Dict[str, Any] = {
            "timestamp_ms": ts_ms,
            "phase": phase,
            "event": event,
        }
        if instance is not None:
            payload["instance"] = instance
        if config_id is not None:
            payload["config_id"] = config_id
        if candidate_config_id is not None:
            payload["candidate_config_id"] = candidate_config_id
        if reason is not None:
            payload["reason"] = reason
        if failure_reason_type is not None:
            payload["failure_reason_type"] = failure_reason_type
        if status is not None:
            payload["status"] = status
        if metrics:
            payload["metrics"] = metrics

        human = human_line
        if human is None:
            parts = [f"[{ts_iso}] {phase}: {event}"]
            if instance:
                parts.append(f"instance={instance}")
            if config_id:
                parts.append(f"config_id={config_id}")
            if candidate_config_id:
                parts.append(f"candidate_config_id={candidate_config_id}")
            if reason:
                parts.append(f"reason={reason}")
            if failure_reason_type:
                parts.append(f"failure_reason_type={failure_reason_type}")
            if status:
                parts.append(f"status={status}")
            if metrics:
                for k, v in list(metrics.items())[:5]:
                    parts.append(f"{k}={v}")
            human = " ".join(parts)

        with _LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(human.rstrip() + "\n")
            if write_jsonl:
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, default=str) + "\n")
        return log_path
    except Exception as e:
        log.debug("append_journal_event: {}", e)
        return None


def write_journal_line(artifacts_root: str, line: str) -> Optional[Path]:
    """Append a single human-readable line to bot_journal.log (no JSONL)."""
    try:
        dir_path = _system_dir(artifacts_root)
        _ensure_dir(dir_path)
        log_path = dir_path / "bot_journal.log"
        ts_iso = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with _LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts_iso}] {line}\n")
        return log_path
    except Exception as e:
        log.debug("write_journal_line: {}", e)
        return None


def get_journal_log_path(artifacts_root: str) -> Path:
    """Return the path to the master bot journal log (for CLI / tail)."""
    return _system_dir(artifacts_root) / "bot_journal.log"


def get_journal_jsonl_path(artifacts_root: str) -> Path:
    """Return the path to the master bot journal JSONL file."""
    return _system_dir(artifacts_root) / "bot_journal.jsonl"
