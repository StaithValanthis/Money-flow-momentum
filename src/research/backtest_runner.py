"""Backtest runner: replay with fill model and produce report."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.research.replay_engine import ReplayEngine
from src.research.fill_model import FillModelConfig, fill_result
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class BacktestReport:
    """Backtest/replay report with assumptions."""

    from_ts: Optional[int] = None
    to_ts: Optional[int] = None
    config_id: Optional[str] = None
    strategy_name: Optional[str] = None
    fill_assumptions: Optional[dict[str, Any]] = None
    total_slippage_usdt: float = 0.0
    total_spread_cost_usdt: float = 0.0
    trade_count: int = 0
    limitations: list[str] = field(default_factory=list)


def run_backtest_replay(
    dataset: dict,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    fill_config: Optional[FillModelConfig] = None,
    config_id: Optional[str] = None,
    strategy_name: Optional[str] = None,
) -> BacktestReport:
    """
    Replay trades with fill model; aggregate slippage and spread cost.
    Does not re-run strategy logic; uses stored trades and applies fill assumptions.
    """
    fill_config = fill_config or FillModelConfig()
    engine = ReplayEngine(dataset)
    trades = engine.replay_trades_in_window(from_ts=from_ts, to_ts=to_ts)
    total_slippage = 0.0
    total_spread = 0.0
    for t in trades:
        price = float(t.get("price") or 0)
        qty = float(t.get("qty") or 0)
        side = str(t.get("side") or "Buy")
        if price <= 0 or qty <= 0:
            continue
        res = fill_result(side, price, qty, fill_config)
        total_slippage += res.slippage_cost_usdt
        total_spread += res.spread_cost_usdt
    return BacktestReport(
        from_ts=from_ts,
        to_ts=to_ts,
        config_id=config_id,
        strategy_name=strategy_name,
        fill_assumptions={
            "slippage_bps": fill_config.slippage_bps,
            "spread_cost_bps": fill_config.spread_cost_bps,
            "partial_fill_pct": fill_config.partial_fill_pct,
        },
        total_slippage_usdt=total_slippage,
        total_spread_cost_usdt=total_spread,
        trade_count=len(trades),
        limitations=[
            "Replay uses stored trades only; no re-scoring.",
            "Fill model is approximate; not tick-accurate.",
            "Entry/exit delay not applied in this report.",
        ],
    )


def write_backtest_report(report: BacktestReport, path: Path) -> None:
    """Write backtest report to JSON and optional Markdown."""
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "from_ts": report.from_ts,
                "to_ts": report.to_ts,
                "config_id": report.config_id,
                "strategy_name": report.strategy_name,
                "fill_assumptions": report.fill_assumptions,
                "total_slippage_usdt": report.total_slippage_usdt,
                "total_spread_cost_usdt": report.total_spread_cost_usdt,
                "trade_count": report.trade_count,
                "limitations": report.limitations,
            },
            f,
            indent=2,
        )
    with open(path.with_suffix(".md"), "w", encoding="utf-8") as f:
        f.write("# Backtest Replay Report\n\n")
        f.write(f"- From: {report.from_ts} To: {report.to_ts}\n")
        f.write(f"- Config: {report.config_id or 'N/A'}\n")
        f.write(f"- Strategy: {report.strategy_name or 'N/A'}\n")
        f.write(f"- Trade count: {report.trade_count}\n")
        f.write(f"- Total slippage (model): {report.total_slippage_usdt:.2f} USDT\n")
        f.write(f"- Total spread cost (model): {report.total_spread_cost_usdt:.2f} USDT\n")
        f.write("\n## Assumptions\n\n")
        f.write(str(report.fill_assumptions) + "\n\n")
        f.write("## Limitations\n\n")
        for L in report.limitations:
            f.write(f"- {L}\n")
