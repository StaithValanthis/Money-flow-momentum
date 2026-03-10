"""Heartbeat: periodic health record for persistence."""

import json
import time
from pathlib import Path
from typing import Any, Optional

from src.monitoring.health import HealthSnapshot
from src.utils.logging import get_logger

log = get_logger(__name__)


def write_heartbeat(
    health: HealthSnapshot,
    path: Path,
) -> None:
    """Write current health snapshot to file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(health.to_dict(), f, indent=2)
    except Exception as e:
        log.error(f"Heartbeat write failed: {e}")


def read_heartbeat(path: Path) -> Optional[dict[str, Any]]:
    """Read last heartbeat. Returns None if missing or invalid."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
