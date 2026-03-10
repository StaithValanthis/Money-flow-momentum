"""Config versioning: registry, lifecycle, fingerprinting, activation, rollback."""

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from src.config.config import Config
from src.utils.logging import get_logger

log = get_logger(__name__)

CONFIG_STATUSES = frozenset({
    "baseline", "candidate", "staged", "active", "rejected", "rolled_back", "archived",
})
CONFIG_SOURCES = frozenset({
    "manual", "optimizer", "shadow", "rollback", "bootstrap",
})


def _config_to_dict(c: Config) -> dict:
    """Serialize config to a deterministic dict for hashing (model_dump sorted)."""
    return c.model_dump(mode="json")


def compute_config_hash(config: Config) -> str:
    """Deterministic hash of effective config (excluding volatile fields if any)."""
    d = _config_to_dict(config)
    canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _get_db(db_path: str):
    from src.storage.db import Database
    return Database(db_path)


def ensure_stage3_schema(db_path: str) -> None:
    from src.storage.migrations import run_stage3_migrations
    run_stage3_migrations(db_path)


def register_config_version(
    config: Config,
    version: str,
    status: str,
    description: str = "",
    source: str = "manual",
    parent_config_id: Optional[str] = None,
    db_path: str = "data/bot.db",
    artifact_dir: Optional[Path] = None,
) -> str:
    """Register a config version. Writes artifact to disk. Returns config_id."""
    ensure_stage3_schema(db_path)
    config_hash = compute_config_hash(config)
    config_id = f"{config_hash}_{int(time.time() * 1000)}"
    created_at = int(time.time() * 1000)
    if status not in CONFIG_STATUSES:
        status = "candidate"
    if source not in CONFIG_SOURCES:
        source = "manual"

    artifact_path = None
    if artifact_dir is None:
        artifact_dir = Path("artifacts/configs")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = str(artifact_dir / f"{config_id}.yaml")
    try:
        import yaml
        with open(artifact_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(_config_to_dict(config), f, default_flow_style=False, sort_keys=True)
    except Exception as e:
        log.warning(f"Could not write config artifact: {e}")
        artifact_path = None

    db = _get_db(db_path)
    conn = db._get_conn()
    conn.execute(
        """INSERT INTO config_versions
           (config_id, version, created_at, parent_config_id, status, description, config_hash, source, artifact_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (config_id, version, created_at, parent_config_id, status, description, config_hash, source, artifact_path),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config_artifacts (config_id, path, created_at) VALUES (?, ?, ?)",
        (config_id, artifact_path or "", created_at),
    )
    conn.commit()
    db.close()
    return config_id


def list_config_versions(
    status: Optional[str] = None,
    limit: int = 100,
    db_path: str = "data/bot.db",
) -> list[dict]:
    """List config versions, optionally by status."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM config_versions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM config_versions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = [dict(r) for r in rows]
    db.close()
    return out


def get_active_config_id(db_path: str = "data/bot.db") -> Optional[str]:
    """Return config_id of the active config, or None."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    row = conn.execute("SELECT config_id FROM config_versions WHERE status = ? LIMIT 1", ("active",)).fetchone()
    db.close()
    return row[0] if row else None


def get_config_version(config_id: str, db_path: str = "data/bot.db") -> Optional[dict]:
    """Get a single config version by config_id."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM config_versions WHERE config_id = ?", (config_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def load_config_from_artifact(config_id: str, db_path: str = "data/bot.db") -> Optional[Config]:
    """Load Config from artifact file. Returns None if not found."""
    rec = get_config_version(config_id, db_path)
    if not rec or not rec.get("artifact_path"):
        return None
    path = Path(rec["artifact_path"])
    if not path.exists():
        return None
    from src.config.config import load_config
    config, _ = load_config(path)
    return config


def activate_config_version(config_id: str, db_path: str = "data/bot.db") -> bool:
    """Set config as active; demote current active to baseline or archived."""
    ensure_stage3_schema(db_path)
    rec = get_config_version(config_id, db_path)
    if not rec:
        return False
    db = _get_db(db_path)
    conn = db._get_conn()
    prev = conn.execute("SELECT config_id FROM config_versions WHERE status = ? LIMIT 1", ("active",)).fetchone()
    conn.execute("UPDATE config_versions SET status = ? WHERE status = ?", ("archived", "active"))
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("active", config_id))
    conn.commit()
    if prev:
        prev_id = prev[0]
        if prev_id != config_id:
            conn.execute(
                "INSERT INTO promotion_events (promoted_config_id, previous_active_config_id, promoted_at, reason, manual) VALUES (?, ?, ?, ?, ?)",
                (config_id, prev_id, int(time.time() * 1000), "activate_config_version", 1),
            )
            conn.commit()
    db.close()
    return True


def stage_config_version(config_id: str, db_path: str = "data/bot.db") -> bool:
    """Mark config as staged (ready for promotion)."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("staged", config_id))
    conn.commit()
    db.close()
    return True


def reject_config_version(config_id: str, db_path: str = "data/bot.db") -> bool:
    """Mark config as rejected."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("rejected", config_id))
    conn.commit()
    db.close()
    return True


def rollback_to_previous_config(db_path: str = "data/bot.db", reason: str = "manual rollback") -> Optional[str]:
    """Set previous active (from last promotion) back to active. Returns new active config_id or None."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    row = conn.execute(
        "SELECT previous_active_config_id, promoted_config_id FROM promotion_events ORDER BY promoted_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        db.close()
        return None
    prev_active_id = row[0]
    current_id = row[1]
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("archived", current_id))
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("rolled_back", current_id))
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("active", prev_active_id))
    conn.execute(
        "INSERT INTO rollback_events (rolled_back_to_config_id, previous_active_config_id, rolled_back_at, reason) VALUES (?, ?, ?, ?)",
        (prev_active_id, current_id, int(time.time() * 1000), reason),
    )
    conn.commit()
    db.close()
    return prev_active_id


def rollback_to_config(config_id: str, db_path: str = "data/bot.db", reason: str = "rollback to specified config") -> bool:
    """Make config_id the active config (explicit rollback target)."""
    ensure_stage3_schema(db_path)
    current = get_active_config_id(db_path)
    if not current:
        activate_config_version(config_id, db_path)
        return True
    db = _get_db(db_path)
    conn = db._get_conn()
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("archived", current))
    conn.execute("UPDATE config_versions SET status = ? WHERE config_id = ?", ("active", config_id))
    conn.execute(
        "INSERT INTO rollback_events (rolled_back_to_config_id, previous_active_config_id, rolled_back_at, reason) VALUES (?, ?, ?, ?)",
        (config_id, current, int(time.time() * 1000), reason),
    )
    conn.commit()
    db.close()
    return True


def diff_config_versions(
    from_config_id: str,
    to_config_id: str,
    db_path: str = "data/bot.db",
) -> dict:
    """Return dict of key paths that differ between two configs (from -> to)."""
    c_from = load_config_from_artifact(from_config_id, db_path)
    c_to = load_config_from_artifact(to_config_id, db_path)
    if not c_from or not c_to:
        return {"error": "config not found"}
    d_from = _config_to_dict(c_from)
    d_to = _config_to_dict(c_to)
    diffs = {}
    all_keys = set(d_from.keys()) | set(d_to.keys())
    for k in sorted(all_keys):
        v1 = d_from.get(k)
        v2 = d_to.get(k)
        if v1 != v2:
            if isinstance(v1, dict) and isinstance(v2, dict):
                sub = {}
                for sk in set(v1.keys()) | set(v2.keys()):
                    if v1.get(sk) != v2.get(sk):
                        sub[sk] = {"from": v1.get(sk), "to": v2.get(sk)}
                if sub:
                    diffs[k] = sub
            else:
                diffs[k] = {"from": v1, "to": v2}
    return diffs
