"""Simple archival/rotation of old artifacts."""

import time
from pathlib import Path
from typing import Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


def rotate_artifacts(
    directory: Path,
    keep_latest: int = 10,
    max_age_seconds: Optional[float] = None,
) -> int:
    """
    Keep only keep_latest most recent items (by mtime) in directory.
    If max_age_seconds set, also remove items older than that.
    Returns count of removed items.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    entries = list(directory.iterdir())
    # Sort by mtime descending
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    now = time.time()
    for i, p in enumerate(entries):
        if i >= keep_latest:
            try:
                if p.is_file():
                    p.unlink()
                else:
                    import shutil
                    shutil.rmtree(p)
                removed += 1
            except Exception as e:
                log.warning(f"Rotate remove failed {p}: {e}")
            continue
        if max_age_seconds and (now - p.stat().st_mtime) > max_age_seconds:
            try:
                if p.is_file():
                    p.unlink()
                else:
                    import shutil
                    shutil.rmtree(p)
                removed += 1
            except Exception as e:
                log.warning(f"Rotate remove failed {p}: {e}")
    return removed
