"""Health snapshots for key runtime loops (WS, context refresh, lifecycle, etc.)."""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class LoopHealth:
    """Health for one loop."""

    name: str
    last_ok_ts: float = 0.0
    last_fail_ts: float = 0.0
    consecutive_failures: int = 0
    status: str = "unknown"  # ok | stale | fail
    message: Optional[str] = None


class HealthSnapshot:
    """Machine-readable health snapshot."""

    def __init__(self):
        self._loops: dict[str, LoopHealth] = {}
        self._ts: float = 0.0
        self._meta: dict[str, Any] = {}

    def set_meta(self, key: str, value: Any) -> None:
        """Set optional metadata (e.g. config_id, strategy_name) for persistence."""
        self._meta[key] = value

    def register(self, name: str) -> None:
        if name not in self._loops:
            self._loops[name] = LoopHealth(name=name)

    def report_ok(self, name: str, message: Optional[str] = None) -> None:
        self.register(name)
        h = self._loops[name]
        h.last_ok_ts = time.time()
        h.consecutive_failures = 0
        h.status = "ok"
        h.message = message

    def report_fail(self, name: str, message: Optional[str] = None) -> None:
        self.register(name)
        h = self._loops[name]
        h.last_fail_ts = time.time()
        h.consecutive_failures += 1
        h.status = "fail"
        h.message = message

    def report_stale(self, name: str, max_age_sec: float) -> None:
        self.register(name)
        h = self._loops[name]
        age = time.time() - h.last_ok_ts
        if age > max_age_sec:
            h.status = "stale"
            h.message = f"last_ok {age:.0f}s ago"
        else:
            h.status = "ok"
            h.message = None

    def to_dict(self) -> dict[str, Any]:
        self._ts = time.time()
        out: dict[str, Any] = {
            "ts": self._ts,
            "loops": {
                k: {
                    "last_ok_ts": v.last_ok_ts,
                    "last_fail_ts": v.last_fail_ts,
                    "consecutive_failures": v.consecutive_failures,
                    "status": v.status,
                    "message": v.message,
                }
                for k, v in self._loops.items()
            },
        }
        if self._meta:
            out["meta"] = self._meta
        return out

    def get_loop(self, name: str) -> Optional[LoopHealth]:
        return self._loops.get(name)
