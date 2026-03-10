"""Safe schema migrations for Stage 3. Does not drop or break existing tables."""

import sqlite3
from pathlib import Path

from src.utils.logging import get_logger

from src.storage.schema_stage3 import STAGE3_SCHEMA, STAGE3_ALTERS
from src.storage.schema_burnin import BURNIN_SCHEMA

log = get_logger(__name__)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        if row[1] == column:
            return True
    return False


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def run_stage3_migrations(db_path: str) -> None:
    """Apply Stage 3 schema: new tables and optional new columns on existing tables."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(STAGE3_SCHEMA)
        conn.commit()
        conn.executescript(BURNIN_SCHEMA)
        conn.commit()
        for sql in STAGE3_ALTERS:
            # ALTER TABLE table ADD COLUMN col TYPE;
            parts = sql.replace(";", "").strip().split()
            if len(parts) >= 6 and parts[0].upper() == "ALTER" and parts[1].upper() == "TABLE":
                table = parts[2]
                col = parts[5]
                if not _table_exists(conn, table):
                    continue
                if _column_exists(conn, table, col):
                    continue
            try:
                conn.execute(sql)
                conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass
                else:
                    log.warning(f"Migration step: {e}")
        conn.commit()
    finally:
        conn.close()
