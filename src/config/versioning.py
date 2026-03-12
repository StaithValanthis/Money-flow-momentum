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
    "manual", "optimizer", "shadow", "rollback", "bootstrap", "demo_import", "warm_start",
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
    return _config_from_artifact_yaml(path)


def _config_from_artifact_yaml(artifact_path: Path) -> Optional[Config]:
    """Load Config from an artifact YAML file (no env merge). Used for cross-instance import."""
    if not artifact_path.exists():
        return None
    try:
        import yaml
        with open(artifact_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            return None
        return Config.model_validate(data)
    except Exception as e:
        log.warning(f"Failed to load config from artifact {artifact_path}: {e}")
        return None


def get_config_version_by_hash(config_hash: str, db_path: str = "data/bot.db") -> Optional[dict]:
    """Find a config version in the registry by config_hash. Returns first match (any status)."""
    ensure_stage3_schema(db_path)
    db = _get_db(db_path)
    conn = db._get_conn()
    row = conn.execute(
        "SELECT * FROM config_versions WHERE config_hash = ? ORDER BY created_at DESC LIMIT 1",
        (config_hash,),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def activate_config_version(
    config_id: str,
    db_path: str = "data/bot.db",
    reason: Optional[str] = None,
    manual: bool = True,
) -> bool:
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
                (config_id, prev_id, int(time.time() * 1000), reason or "activate_config_version", 1 if manual else 0),
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


def import_candidate_to_live(
    candidate_config_id: str,
    demo_db_path: str,
    live_db_path: str,
    live_artifact_dir: Path,
    description: str = "",
    reason: str = "",
    activate: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Import a candidate config from the Demo instance into the Live instance.
    Reads the candidate from Demo DB and artifact, registers it in Live (or reuses existing by hash).
    Does not activate unless activate=True.
    Returns a result dict with ok, error, candidate_config_id, live_config_id, imported, already_present, activated.
    """
    result: dict = {
        "ok": False,
        "error": None,
        "candidate_config_id": candidate_config_id,
        "live_config_id": None,
        "imported": False,
        "already_present": False,
        "activated": False,
        "dry_run": dry_run,
    }
    rec = get_config_version(candidate_config_id, demo_db_path)
    if not rec:
        result["error"] = f"Candidate '{candidate_config_id}' not found in Demo DB ({demo_db_path})."
        return result
    artifact_path = Path(rec["artifact_path"]) if rec.get("artifact_path") else None
    if not artifact_path or not artifact_path.exists():
        result["error"] = f"Demo artifact not found: {rec.get('artifact_path')}. Run from repo root or check path."
        return result
    config = _config_from_artifact_yaml(artifact_path)
    if not config:
        result["error"] = "Could not load candidate config from artifact (invalid YAML or schema)."
        return result
    config_hash = compute_config_hash(config)
    existing = get_config_version_by_hash(config_hash, live_db_path)
    if existing:
        live_config_id = existing["config_id"]
        result["already_present"] = True
        result["live_config_id"] = live_config_id
    else:
        if dry_run:
            result["ok"] = True
            result["live_config_id"] = f"(would create: {config_hash}_{int(time.time() * 1000)})"
            result["imported"] = True
            return result
        live_artifact_dir = Path(live_artifact_dir)
        live_artifact_dir.mkdir(parents=True, exist_ok=True)
        live_config_id = register_config_version(
            config,
            version="imported",
            status="candidate",
            description=description or f"Imported from Demo candidate {candidate_config_id}",
            source="demo_import",
            parent_config_id=None,
            db_path=live_db_path,
            artifact_dir=live_artifact_dir,
        )
        result["live_config_id"] = live_config_id
        result["imported"] = True
    result["ok"] = True
    if activate and not dry_run:
        if activate_config_version(live_config_id, live_db_path, reason=reason or "promote-to-live"):
            result["activated"] = True
    return result
