"""Basic backtest using saved signals and trades."""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from src.storage.db import Database
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class BacktestResult:
    """Backtest summary."""

    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    win_rate: float
    expectancy: float
    max_drawdown_pct: float
    sharpe_like: float


class BacktestRunner:
    """Replay saved trades and evaluate performance."""

    def __init__(self, db_path: str = "data/bot.db"):
        self.db = Database(db_path)

    def run(self, since_ts: Optional[int] = None) -> BacktestResult:
        """Run backtest on saved trades."""
        trades = self.db.get_trades(since_ts)
        if not trades:
            return BacktestResult(
                total_trades=0, wins=0, losses=0, total_pnl=0,
                win_rate=0, expectancy=0, max_drawdown_pct=0, sharpe_like=0,
            )

        pnls = []
        equity = 10_000.0
        peak = equity
        max_dd = 0.0

        for t in trades:
            pnl = float(t.get("pnl", 0) or 0)
            pnls.append(pnl)
            equity += pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        pnls_arr = np.array(pnls)
        wins = int(np.sum(pnls_arr > 0))
        losses = int(np.sum(pnls_arr < 0))
        total_pnl = float(np.sum(pnls_arr))
        win_rate = wins / len(pnls) if pnls else 0
        expectancy = total_pnl / len(pnls) if pnls else 0
        sharpe = float(np.mean(pnls_arr) / np.std(pnls_arr) * np.sqrt(252)) if np.std(pnls_arr) > 0 else 0

        return BacktestResult(
            total_trades=len(trades),
            wins=wins,
            losses=losses,
            total_pnl=total_pnl,
            win_rate=win_rate,
            expectancy=expectancy,
            max_drawdown_pct=max_dd,
            sharpe_like=sharpe,
        )
