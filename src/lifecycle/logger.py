"""Unified Demo lifecycle log: one append-only stream for init, warmup, probation, runtime, reinit (Demo-only)."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()


def _lifecycle_dir(artifacts_root: str, instance_name: Optional[str]) -> Path:
    """Return artifacts/<instance>/lifecycle dir; avoid double-nesting if artifacts_root already has instance."""
    base = Path(artifacts_root)
    instance = instance_name or "demo"
    if instance in base.parts:
        return base / "lifecycle"
    return base / instance / "lifecycle"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def append_demo_lifecycle_event(
    artifacts_root: str,
    instance_name: Optional[str],
    phase: str,
    event: str,
    *,
    config_id: Optional[str] = None,
    candidate_config_id: Optional[str] = None,
    reason: Optional[str] = None,
    failure_reason_type: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
    human_line: Optional[str] = None,
    write_jsonl: bool = True,
) -> Optional[Path]:
    """
    Append one event to the unified Demo lifecycle log (and optionally JSONL).
    Safe to call from different phases/processes (append-only, locked).
    """
    try:
        dir_path = _lifecycle_dir(artifacts_root, instance_name)
        _ensure_dir(dir_path)
        log_path = dir_path / "demo_lifecycle.log"
        jsonl_path = dir_path / "demo_lifecycle.jsonl"

        ts = time.time()
        ts_ms = int(ts * 1000)
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        payload: Dict[str, Any] = {
            "timestamp_ms": ts_ms,
            "phase": phase,
            "event": event,
        }
        if config_id is not None:
            payload["config_id"] = config_id
        if candidate_config_id is not None:
            payload["candidate_config_id"] = candidate_config_id
        if reason is not None:
            payload["reason"] = reason
        if failure_reason_type is not None:
            payload["failure_reason_type"] = failure_reason_type
        if metrics:
            payload["metrics"] = metrics

        human = human_line
        if human is None:
            parts = [f"[{ts_iso}] {phase}: {event}"]
            if config_id:
                parts.append(f"config_id={config_id}")
            if candidate_config_id:
                parts.append(f"candidate_config_id={candidate_config_id}")
            if reason:
                parts.append(f"reason={reason}")
            if failure_reason_type:
                parts.append(f"failure_reason_type={failure_reason_type}")
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
        # Also write to master bot journal (single system-wide log)
        try:
            from src.journal.logger import append_journal_event as journal_append
            journal_append(
                artifacts_root, phase, event,
                instance=instance_name or "demo",
                config_id=config_id,
                candidate_config_id=candidate_config_id,
                reason=reason,
                failure_reason_type=failure_reason_type,
                metrics=metrics,
                write_jsonl=write_jsonl,
            )
        except Exception:
            pass
        return log_path
    except Exception as e:
        log.debug("append_demo_lifecycle_event: {}", e)
        return None


def write_human_log_line(
    artifacts_root: str,
    instance_name: Optional[str],
    line: str,
) -> Optional[Path]:
    """Append a single human-readable line to demo_lifecycle.log (no JSONL)."""
    try:
        dir_path = _lifecycle_dir(artifacts_root, instance_name)
        _ensure_dir(dir_path)
        log_path = dir_path / "demo_lifecycle.log"
        ts_iso = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with _LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts_iso}] {line}\n")
        return log_path
    except Exception as e:
        log.debug("write_human_log_line: {}", e)
        return None


def get_demo_lifecycle_log_path(artifacts_root: str, instance_name: Optional[str]) -> Path:
    """Return the path to the Demo lifecycle log (for CLI / tail)."""
    return _lifecycle_dir(artifacts_root, instance_name) / "demo_lifecycle.log"


def get_demo_lifecycle_jsonl_path(artifacts_root: str, instance_name: Optional[str]) -> Path:
    """Return the path to the Demo lifecycle JSONL file."""
    return _lifecycle_dir(artifacts_root, instance_name) / "demo_lifecycle.jsonl"
