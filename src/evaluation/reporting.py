"""Write evaluation artifacts: JSON, CSV, Markdown."""

import csv
import json
from pathlib import Path
from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)


def write_evaluation_artifacts(summary: dict, artifact_dir: Path, run_id: str) -> Path:
    """Write summary.json, metrics_by_symbol.csv, evaluation_report.md. Returns report path."""
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"evaluation_{run_id}"

    with open(artifact_dir / f"{prefix}_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    core = summary.get("core") or {}
    by_symbol = summary.get("by_symbol") or {}
    if by_symbol:
        path_csv = artifact_dir / f"{prefix}_metrics_by_symbol.csv"
        with open(path_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "trade_count", "total_pnl", "return_pct", "max_drawdown", "win_rate", "expectancy", "sharpe_like"])
            for sym, m in by_symbol.items():
                w.writerow([
                    sym,
                    m.get("trade_count", 0),
                    m.get("total_pnl", 0),
                    m.get("return_pct", 0),
                    m.get("max_drawdown", 0),
                    m.get("win_rate", 0),
                    m.get("expectancy", 0),
                    m.get("sharpe_like", 0),
                ])

    report_path = artifact_dir / f"{prefix}_report.md"
    lines = [
        "# Evaluation Report",
        f"Run ID: {run_id}",
        f"From: {summary.get('from_ts')} To: {summary.get('to_ts')}",
        f"Config ID: {summary.get('config_id') or 'any'}",
        "",
        "## Core Metrics",
        f"- Trade count: {core.get('trade_count', 0)}",
        f"- Total PnL: {core.get('total_pnl', 0):.2f}",
        f"- Return %: {core.get('return_pct', 0):.2f}",
        f"- Max drawdown %: {core.get('max_drawdown', 0):.2f}",
        f"- Win rate: {core.get('win_rate', 0):.2%}",
        f"- Expectancy: {core.get('expectancy', 0):.2f}",
        f"- Sharpe-like: {core.get('sharpe_like', 0):.2f}",
        "",
        "## Diagnostic",
    ]
    diag = summary.get("diagnostic") or {}
    for k, v in diag.items():
        if k != "rejection_reason_counts" and k != "lifecycle_event_counts":
            lines.append(f"- {k}: {v}")
    stage4 = summary.get("stage4") or {}
    if stage4:
        lines.append("")
        lines.append("## Stage 4 Metrics")
        lines.append("- Exit reason counts: " + str(stage4.get("exit_reason_counts", {})))
        lines.append("- Regime label counts: " + str(stage4.get("regime_label_counts", {})))
        lines.append("- Threshold profile counts: " + str(stage4.get("threshold_profile_counts", {})))
        lines.append("- Stage 4 rejection counts: " + str(stage4.get("stage4_rejection_counts", {})))
        by_exit = stage4.get("by_exit_reason_metrics") or {}
        for reason, m in by_exit.items():
            lines.append(f"- Exit reason `{reason}`: event_count={m.get('event_count', 0)}")
    stage5_portfolio = summary.get("stage5_portfolio") or {}
    if stage5_portfolio:
        lines.append("")
        lines.append("## Stage 5 Portfolio")
        lines.append(f"- Cluster block count: {stage5_portfolio.get('cluster_block_count', 0)}")
        lines.append(f"- Budget block count: {stage5_portfolio.get('budget_block_count', 0)}")
        lines.append(f"- Resized by allocation count: {stage5_portfolio.get('resized_by_allocation_count', 0)}")
        lines.append("- Allocation method usage: " + str(stage5_portfolio.get("allocation_method_usage", {})))
        lines.append("- Stage 5 rejection counts: " + str(stage5_portfolio.get("stage5_rejection_counts", {})))
    fill_quality = summary.get("fill_quality") or {}
    if fill_quality:
        lines.append("")
        lines.append("## Fill quality (execution audit)")
        for k, v in fill_quality.items():
            if v is not None:
                lines.append(f"- {k}: {v}")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    if stage4:
        path_stage4 = artifact_dir / f"{prefix}_stage4.csv"
        with open(path_stage4, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["metric", "key", "value"])
            for k, v in (stage4.get("exit_reason_counts") or {}).items():
                w.writerow(["exit_reason_count", k, v])
            for k, v in (stage4.get("regime_label_counts") or {}).items():
                w.writerow(["regime_label_count", k, v])
            for k, v in (stage4.get("threshold_profile_counts") or {}).items():
                w.writerow(["threshold_profile_count", k, v])
            for k, v in (stage4.get("stage4_rejection_counts") or {}).items():
                w.writerow(["stage4_rejection_count", k, v])

    return report_path
