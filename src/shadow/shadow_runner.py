"""Shadow runner: run candidate config in parallel with baseline (no live orders)."""

import time
from pathlib import Path
from typing import Optional

from src.storage.db import Database
from src.config.versioning import get_active_config_id, load_config_from_artifact
from src.config.config import Config
from src.utils.logging import get_logger

log = get_logger(__name__)


class ShadowRunner:
    """
    Runs a candidate config in shadow mode: same inputs as baseline, decisions logged but not executed.
    """

    def __init__(self, db_path: str = "data/bot.db"):
        self.db = Database(db_path)
        self._shadow_run_id: Optional[int] = None
        self._candidate_config_id: Optional[str] = None
        self._baseline_config_id: Optional[str] = None

    def start(self, candidate_config_id: str) -> bool:
        """Start a shadow run for the given candidate. Returns True if started."""
        baseline_id = get_active_config_id(self.db.path)
        conn = self.db._get_conn()
        now = int(time.time() * 1000)
        try:
            conn.execute(
                """INSERT INTO shadow_runs (candidate_config_id, started_at, baseline_config_id) VALUES (?, ?, ?)""",
                (candidate_config_id, now, baseline_id),
            )
            conn.commit()
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            self._shadow_run_id = row[0] if row else None
            self._candidate_config_id = candidate_config_id
            self._baseline_config_id = baseline_id
            return self._shadow_run_id is not None
        except Exception as e:
            log.error(f"Shadow start: {e}")
            return False

    def stop(self) -> None:
        """Stop current shadow run."""
        if self._shadow_run_id is None:
            return
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE shadow_runs SET stopped_at = ? WHERE id = ?",
            (int(time.time() * 1000), self._shadow_run_id),
        )
        conn.commit()
        self._shadow_run_id = None
        self._candidate_config_id = None
        self._baseline_config_id = None

    def record_decision(
        self,
        ts: int,
        symbol: str,
        direction: str,
        reason: str,
        score: float,
        baseline_decision: str = "",
        baseline_score: float = 0.0,
    ) -> None:
        """Record a shadow decision (what candidate would do)."""
        if self._shadow_run_id is None:
            return
        try:
            self.db._get_conn().execute(
                """INSERT INTO shadow_decisions (shadow_run_id, ts, symbol, direction, reason, score, baseline_decision, baseline_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._shadow_run_id, ts, symbol, direction, reason, score, baseline_decision, baseline_score),
            )
            self.db._get_conn().commit()
        except Exception as e:
            log.debug(f"Shadow record decision: {e}")

    @property
    def is_running(self) -> bool:
        return self._shadow_run_id is not None

    @property
    def shadow_run_id(self) -> Optional[int]:
        return self._shadow_run_id

    @property
    def candidate_config_id(self) -> Optional[str]:
        return self._candidate_config_id
