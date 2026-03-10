"""Centralized artifact paths and manifest."""

import json
import time
from pathlib import Path
from typing import Any, Optional


def artifacts_root(base: Optional[Path] = None) -> Path:
    """Root directory for all artifacts."""
    return Path(base or "artifacts")


def evaluations_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "evaluations"


def optimizations_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "optimizations"


def configs_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "configs"


def shadow_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "shadow"


def backtest_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "backtest"


def burnin_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "burnin"


def validation_dir(base: Optional[Path] = None) -> Path:
    return artifacts_root(base) / "validation"


def pipeline_dir(base: Optional[Path] = None) -> Path:
    """Directory for post-burn-in / pipeline helper artifacts."""
    return artifacts_root(base) / "pipeline"


def automation_dir(base: Optional[Path] = None) -> Path:
    """Directory for automation / orchestration artifacts."""
    return artifacts_root(base) / "automation"


def ensure_artifact_dirs(base: Optional[Path] = None) -> None:
    """Create standard artifact directories."""
    for d in (
        evaluations_dir(base),
        optimizations_dir(base),
        configs_dir(base),
        shadow_dir(base),
        backtest_dir(base),
        burnin_dir(base),
        validation_dir(base),
        pipeline_dir(base),
        automation_dir(base),
    ):
        d.mkdir(parents=True, exist_ok=True)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write manifest.json alongside artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["_written_ts"] = int(time.time() * 1000)
    with open(path.parent / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
