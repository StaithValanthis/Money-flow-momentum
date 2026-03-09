"""Main bot: boot, run loop, health checks, graceful shutdown."""

import signal
import threading
import time
from pathlib import Path
from typing import Optional

import typer
from loguru import logger

from src.config.config import load_config, Config, EnvSettings
from src.exchange.bybit_client import BybitClient
from src.data.universe import UniverseManager
from src.data.market_state import MarketStateManager
from src.data.feature_builder import FeatureBuilder
from src.signals.flow_impulse import FlowImpulseScorer
from src.risk.risk_engine import RiskEngine
from src.execution.executor import Executor
from src.portfolio.position_manager import PositionManager
from src.storage.db import Database
from src.utils.logging import setup_logging, get_logger

log = get_logger(__name__)

app = typer.Typer()


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self, config: Config, env: EnvSettings):
        self.config = config
        self.env = env
        self.running = False
        self._client: Optional[BybitClient] = None
        self._universe: Optional[UniverseManager] = None
        self._market_state: Optional[MarketStateManager] = None
        self._feature_builder: Optional[FeatureBuilder] = None
        self._scorer: Optional[FlowImpulseScorer] = None
        self._risk: Optional[RiskEngine] = None
        self._executor: Optional[Executor] = None
        self._positions: Optional[PositionManager] = None
        self._db: Optional[Database] = None
        self._ws_thread: Optional[threading.Thread] = None

    def _init_components(self) -> None:
        """Initialize all components."""
        api_key = self.env.bybit_api_key or ""
        api_secret = self.env.bybit_api_secret or ""
        testnet = self.env.bybit_testnet

        self._client = BybitClient(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            config=self.config.exchange,
        )
        self._universe = UniverseManager(self._client, self.config.universe)
        self._market_state = MarketStateManager(self.config.features)
        self._feature_builder = FeatureBuilder(self.config.features)
        self._scorer = FlowImpulseScorer(self.config.score_weights, self.config.entry)
        self._risk = RiskEngine(self.config.risk)
        self._executor = Executor(
            self._client,
            self.config.execution,
            self.config.stop_tp,
        )
        self._positions = PositionManager(self.config.risk)
        self._db = Database(self.config.database_path)

    def _on_trade(self, trade: dict) -> None:
        """Handle incoming trade from WebSocket."""
        self._market_state.on_trade(trade)

    def _on_ticker(self, ticker: dict) -> None:
        """Handle ticker update."""
        symbol = ticker.get("symbol", "")
        bid = float(ticker.get("bid1Price", 0) or 0)
        ask = float(ticker.get("ask1Price", 0) or 0)
        last = float(ticker.get("lastPrice", 0) or 0)
        if symbol:
            self._market_state.update_ticker(symbol, bid, ask, last)

    def _run_ws(self) -> None:
        """Run WebSocket in thread (pybit uses sync)."""
        symbols = self._universe.symbols[:50]  # Limit symbols for WS
        if not symbols:
            return
        self._client.start_public_ws(
            symbols=symbols,
            on_trade=self._on_trade,
            on_ticker=self._on_ticker,
        )
        while self.running:
            time.sleep(1)

    def _scan_loop(self) -> None:
        """Main scan loop: score symbols, check entries."""
        while self.running:
            try:
                symbols = self._universe.symbols
                if not symbols:
                    time.sleep(self.config.scan_interval_seconds)
                    continue

                # Build features
                features_list = []
                for sym in symbols:
                    state = self._market_state.get_state(sym)
                    if state and state.last_price > 0:
                        f = self._feature_builder.build(state)
                        features_list.append(f)

                if not features_list:
                    time.sleep(self.config.scan_interval_seconds)
                    continue

                # Score
                signals = self._scorer.score_all(
                    features_list,
                    max_longs=self.config.risk.max_concurrent_positions,
                    max_shorts=self.config.risk.max_concurrent_positions,
                )

                now_ms = int(time.time() * 1000)
                for sig in signals:
                    self._db.insert_signal(
                        ts=now_ms,
                        symbol=sig.symbol,
                        score=sig.score,
                        direction=sig.direction,
                        delta_1m=sig.delta_1m,
                        buy_sell_ratio=sig.buy_sell_ratio_1m,
                    )

                # Dry run / paper: no execution
                if self.config.mode in ("dry_run", "paper"):
                    if signals:
                        log.info(f"Signals: {[(s.symbol, s.direction, round(s.score, 2)) for s in signals]}")
                else:
                    # Live: execute (simplified - full logic would check positions, risk, etc.)
                    self._execute_signals(signals, now_ms)

            except Exception as e:
                log.error(f"Scan error: {e}")
                self._db.insert_error(int(time.time() * 1000), "main", str(e))

            time.sleep(self.config.scan_interval_seconds)

    def _execute_signals(self, signals: list, now_ms: int) -> None:
        """Execute signals (live mode)."""
        for sig in signals:
            if sig.direction == "none":
                continue
            ok, reason = self._positions.can_trade_symbol(sig.symbol, now_ms)
            if not ok:
                continue
            ok, reason = self._risk.can_open_position(self._positions.count(), sig.symbol)
            if not ok:
                continue
            # Position sizing and entry would go here
            log.info(f"Would execute: {sig.symbol} {sig.direction} score={sig.score:.2f}")

    def run(self) -> None:
        """Run the bot."""
        self._init_components()
        self.running = True

        # Refresh universe
        self._universe.refresh()
        if not self._universe.symbols:
            log.error("No symbols in universe")
            return

        # Set position mode
        if self.config.exchange.one_way_mode:
            try:
                self._client.set_position_mode(mode=0)
            except Exception as e:
                log.warning(f"Set position mode: {e}")

        # Start WebSocket in background thread
        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self._ws_thread.start()

        # Allow time for WS to connect
        time.sleep(3)

        # Run scan loop
        try:
            self._scan_loop()
        finally:
            self.running = False
            self._client.stop_public_ws()
            self._db.close()


@app.command()
def run(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Run the trading bot."""
    config, env = load_config(config_path)
    setup_logging(
        level=config.logging.level,
        log_file=config.logging.log_file,
        rotation=config.logging.rotation,
        retention=config.logging.retention,
    )
    logger.info(f"Starting bot mode={config.mode} testnet={env.bybit_testnet}")

    bot = TradingBot(config, env)

    def shutdown(sig, frame):
        logger.info("Shutdown requested")
        bot.running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    bot.run()


if __name__ == "__main__":
    app()
