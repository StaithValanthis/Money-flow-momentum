"""Main bot: boot, run loop, health checks, graceful shutdown."""

import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Any

import typer
from loguru import logger

from src.config.config import load_config, Config, EnvSettings, resolve_bybit_credentials, get_bybit_env, get_effective_equity_for_sizing, get_effective_operating_mode
from src.config.versioning import get_active_config_id, ensure_stage3_schema, load_config_from_artifact, register_config_version
from src.exchange.bybit_client import BybitClient
from src.exchange.ws_shard import PublicWSShardManager
from src.data.universe import UniverseManager
from src.data.market_state import MarketStateManager
from src.data.feature_builder import FeatureBuilder
from src.data.context_refresher import ContextRefresher
from src.data.eligibility import check_eligibility
from src.signals.flow_impulse import FlowImpulseScorer
from src.signals.regime_filters import classify_regime
from src.signals.threshold_policy import compute_adaptive_thresholds
from src.portfolio.correlation import cluster_by_correlation_proxy
from src.portfolio.risk_budget import build_budget_state
from src.portfolio.allocator import allocate_risk, allocate_candidate_set, AllocationDecision, CandidateForAllocation
from src.risk.risk_engine import RiskEngine
from src.execution.executor import Executor
from src.portfolio.position_manager import PositionManager, TrackedPosition
from src.portfolio.lifecycle import LifecycleManager, LifecycleState, LifecyclePhase
from src.storage.db import Database
from src.storage.reconciliation import ReconciliationStore
from src.storage.artifacts import ensure_artifact_dirs
from src.execution.audit import record_entry_intent, record_fill
from src.validation.burn_in import check_burnin_gates
from src.validation.protection_audit import run_protection_audit
from src.utils.logging import setup_logging, get_logger
from src.cli.stage3_commands import register_stage3_cli

log = get_logger(__name__)

# Exit code when Demo probation failed and auto_reinit_after_failure is True (wrapper may re-run demo init)
EXIT_PROBATION_REINIT = 20

app = typer.Typer()
register_stage3_cli(app)


class TradingBot:
    """Fully wired trading bot: boot, continuous loop, shutdown."""

    def __init__(self, config: Config, env: EnvSettings):
        self.config = config
        self.env = env
        self.running = False
        self._client: Optional[BybitClient] = None
        self._universe: Optional[UniverseManager] = None
        self._market_state: Optional[MarketStateManager] = None
        self._feature_builder: Optional[FeatureBuilder] = None
        self._context: Optional[ContextRefresher] = None
        self._scorer: Optional[FlowImpulseScorer] = None
        self._risk: Optional[RiskEngine] = None
        self._executor: Optional[Executor] = None
        self._positions: Optional[PositionManager] = None
        self._lifecycle: Optional[LifecycleManager] = None
        self._db: Optional[Database] = None
        self._recon: Optional[ReconciliationStore] = None
        self._ws_shards: Optional[PublicWSShardManager] = None
        self._equity_usdt: float = 10_000.0
        self._available_balance_usdt: Optional[float] = None
        self._startup_sync_done = False
        self._config_id: Optional[str] = None
        self._last_degradation_check_ts: float = 0.0
        self._health: Optional[Any] = None
        self._heartbeat_path: Optional[Path] = None
        self._last_heartbeat_write_ts: float = 0.0
        self._effective_api_key: str = ""  # set in _init_components; never log
        self._reinit_requested: bool = False  # set when probation failed + auto_reinit_after_failure (Demo-only)

    def _init_components(self) -> None:
        env_type = get_bybit_env(self.env)
        api_key, api_secret, is_legacy, _ = resolve_bybit_credentials(self.env, env_type)
        self._effective_api_key = api_key
        self._bybit_env_type = env_type  # demo | live | testnet
        cred_mode = "legacy" if is_legacy else "dual_key"
        log.info("Bybit environment={} credential_mode={}", env_type, cred_mode)
        if is_legacy:
            log.warning("Using legacy BYBIT_API_KEY/SECRET; set BYBIT_DEMO_API_KEY/SECRET and BYBIT_LIVE_API_KEY/SECRET for dual-key mode")
        if env_type == "demo":
            log.info("Demo mode: REST/private WS = demo endpoints; public WS = mainnet")
        testnet = env_type == "testnet"
        demo = env_type == "demo"
        self._client = BybitClient(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            demo=demo,
            config=self.config.exchange,
        )
        self._universe = UniverseManager(self._client, self.config.universe)
        self._market_state = MarketStateManager(self.config.features)
        self._feature_builder = FeatureBuilder(self.config.features)
        self._context = ContextRefresher(
            self._client,
            self.config,
            self._market_state,
            self._universe,
        )
        self._scorer = FlowImpulseScorer(self.config.score_weights, self.config.entry)
        self._risk = RiskEngine(self.config.risk, equity_usdt=10_000.0)
        if get_effective_operating_mode(self.config, self.env) == "demo_research":
            dr = getattr(self.config, "demo_research", None)
            if dr and getattr(dr, "relaxed_kill_switch_enabled", False):
                self._risk.set_demo_kill_switch_override(
                    max_drawdown_pct=getattr(dr, "demo_max_daily_drawdown_pct", 15.0),
                    max_realized_loss_usdt=getattr(dr, "demo_max_daily_realized_loss_usdt", 150.0),
                )
        self._executor = Executor(
            self._client,
            self.config.execution,
            self.config.stop_tp,
        )
        self._positions = PositionManager(self.config.risk)
        self._lifecycle = LifecycleManager(self.config, self._market_state)
        self._db = Database(self.config.database_path)
        self._recon = ReconciliationStore()
        ensure_stage3_schema(self.config.database_path)
        ensure_artifact_dirs(self.config.artifacts_root)
        self._heartbeat_path = Path(self.config.artifacts_root) / "heartbeat.json"
        from src.monitoring.health import HealthSnapshot
        self._health = HealthSnapshot()
        for name in ("public_ws", "private_ws", "context_refresh", "reconciliation", "lifecycle", "score_entry", "degradation_monitor"):
            self._health.register(name)
        self._config_id = get_active_config_id(self.config.database_path)
        if not self._config_id:
            self._config_id = register_config_version(
                self.config, version="bootstrap", status="active", description="Bootstrap active", source="bootstrap",
                db_path=self.config.database_path,
            )
            log.info("Registered bootstrap config: {}", self._config_id)
        else:
            loaded = load_config_from_artifact(self._config_id, self.config.database_path)
            if loaded is not None:
                self.config = loaded
            log.info("Using active config: {}", self._config_id)

    def _on_trade(self, trade: dict) -> None:
        self._market_state.on_trade(trade)

    def _on_ticker(self, ticker: dict) -> None:
        symbol = ticker.get("symbol", "")
        bid = float(ticker.get("bid1Price", 0) or 0)
        ask = float(ticker.get("ask1Price", 0) or 0)
        last = float(ticker.get("lastPrice", 0) or 0)
        if symbol:
            self._market_state.update_ticker(symbol, bid, ask, last)

    def _on_order(self, data: dict) -> None:
        self._recon.on_order_update(data)

    def _on_position(self, data: dict) -> None:
        self._recon.on_position_update(data)

    def _on_execution(self, data: dict) -> None:
        """Handle private WS execution: update recon + lifecycle for TP fills; record entry fills for audit; always persist to trades."""
        self._recon.on_execution(data)
        # Bybit V5 linear uses execPnl for realized PnL per close; fallback to closedPnl for other/legacy
        realised_pnl = float(data.get("execPnl") or data.get("closedPnl") or 0)
        if realised_pnl != 0 and self._risk:
            self._risk.record_realized_pnl(realised_pnl)

        order_id = data.get("orderId", "")
        if not order_id:
            return
        qty = float(data.get("execQty", 0) or 0)
        ts = int(data.get("execTime", 0) or time.time() * 1000)
        price = float(data.get("execPrice", 0) or 0)
        if qty <= 0:
            return

        order_rec = self._recon.orders.get(order_id)
        is_tp_fill = False
        if order_rec:
            link = order_rec.order_link_id or ""
            symbol = order_rec.symbol
            side = order_rec.side
            if not symbol:
                return
            if link.startswith("tp1_"):
                self._handle_tp_execution(symbol, "tp1", qty, ts, side, data)
                is_tp_fill = True
            elif link.startswith("tp2_"):
                self._handle_tp_execution(symbol, "tp2", qty, ts, side, data)
                is_tp_fill = True
            else:
                if self._db and link.startswith("entry"):
                    audit_rows = self._db.get_execution_audit(order_id=order_id)
                    intent_price = intent_qty = None
                    if audit_rows:
                        r = audit_rows[0]
                        intent_price = r.get("intent_price")
                        intent_qty = r.get("intent_qty")
                    record_fill(self._db, order_id, qty, price, ts, intent_price=intent_price, intent_qty=intent_qty)
        else:
            symbol = data.get("symbol", "")
            side = data.get("side", "")
            if not symbol:
                log.debug("Execution missing symbol and order not in recon; skip trade persist order_id=%s", order_id)
                return
            link = data.get("orderLinkId", "") or ""

        if is_tp_fill or not self._db:
            return
        try:
            exec_id = data.get("execId", "") or data.get("executionId", "")
            self._db.insert_fill(ts, exec_id, order_id, symbol, side, qty, price, realised_pnl, config_id=getattr(self, "_config_id", None))
            self._db.insert_trade(ts, symbol, side, qty, price, order_id=order_id, order_link_id=link, pnl=realised_pnl, config_id=getattr(self, "_config_id", None))
            try:
                from src.demo_probation import run_probation_fail_fast_check
                if run_probation_fail_fast_check(self.config.database_path, self.config):
                    self._stop_on_probation_failure()
            except Exception as e:
                log.debug("probation fail-fast check: %s", e)
        except Exception as e:
            log.debug("insert_fill/insert_trade: %s", e)

    def _stop_on_probation_failure(self) -> bool:
        """If stop_demo_on_failure is True, log and set self.running = False. Demo-only. Returns True if stopped."""
        prob = getattr(self.config, "demo_probation", None)
        stop = getattr(prob, "stop_demo_on_failure", True)
        if stop:
            reinit = getattr(prob, "auto_reinit_after_failure", False)
            if reinit:
                log.warning("Demo probation failed; stopping Demo runtime (re-init requested)")
                self._reinit_requested = True
            else:
                log.warning("Demo probation failed; stopping Demo runtime")
            self.running = False
            return True
        log.info("Demo probation failed (stop_demo_on_failure=false); continuing")
        return False

    def _fetch_equity(self) -> float:
        try:
            r = self._client.get_wallet_balance(account_type="UNIFIED")
            lst = r.get("result", {}).get("list", [])
            if lst:
                acc = lst[0]
                total = acc.get("totalEquity")
                avail = acc.get("totalAvailableBalance")
                self._available_balance_usdt = float(avail) if avail is not None else None
                if total is not None:
                    return float(total)
        except Exception as e:
            log.warning(f"Fetch equity: {e}")
            self._risk.record_api_error()
        self._available_balance_usdt = None
        return self._equity_usdt

    def _fetch_available_balance(self) -> Optional[float]:
        """Return USDT available balance (USD) for sizing cap, or None if unavailable."""
        return getattr(self, "_available_balance_usdt", None)

    def _tp_fractions(self) -> tuple[float, float]:
        """Return (tp1_fraction, tp2_fraction), scaled if sum > 1."""
        cfg = self.config.stop_tp
        tp1 = cfg.tp1_pct
        tp2 = cfg.tp2_pct
        total = tp1 + tp2
        if total > 1.0 + 1e-6:
            scale = 1.0 / total
            log.warning(
                f"TP fractions sum to {total:.3f} > 1.0; scaling by {scale:.3f}"
            )
            tp1 *= scale
            tp2 *= scale
        return tp1, tp2

    def _ensure_tp_orders_for_position(self, prec) -> None:
        """
        Ensure TP1/TP2 reduce-only orders exist for a given position record.
        Uses reconciled position size so it is robust to partial entry fills.
        """
        symbol = prec.symbol
        lc = self._lifecycle.get(symbol)
        if not lc:
            return
        if lc.tp1_order_id or lc.tp2_order_id:
            return

        if self.config.dry_run:
            now_ms = int(time.time() * 1000)
            self._db.insert_lifecycle_event(
                now_ms, symbol, "tp_plan_dry_run", lc.phase.value, config_id=self._config_id
            )
            return

        entry_price = prec.entry_price
        stop_loss = prec.stop_loss
        size = prec.size
        side = prec.side
        if stop_loss <= 0 or size <= 0 or entry_price <= 0:
            return

        R = abs(entry_price - stop_loss)
        if R <= 0:
            return

        cfg = self.config.stop_tp
        tp1_frac, tp2_frac = self._tp_fractions()

        # TP prices based on R-multiples
        if side == "Buy":
            raw_tp1 = entry_price + R * cfg.tp1_r_multiple
            raw_tp2 = entry_price + R * cfg.tp2_r_multiple
            close_side = "Sell"
        else:
            raw_tp1 = entry_price - R * cfg.tp1_r_multiple
            raw_tp2 = entry_price - R * cfg.tp2_r_multiple
            close_side = "Buy"

        tick = self._universe.get_tick_size(symbol) or 0.01

        def round_price(p: float) -> float:
            return round(p / tick) * tick

        tp1_price = round_price(raw_tp1)
        tp2_price = round_price(raw_tp2)

        # Quantities
        qty_step = self._universe.get_qty_step(symbol)
        min_qty = self._universe.get_min_qty(symbol)
        min_notional = self._universe.get_min_notional(symbol)

        def round_qty(q: float) -> float:
            if qty_step <= 0:
                return q
            return round(q / qty_step) * qty_step

        full_qty = size
        tp1_raw = full_qty * tp1_frac
        tp2_raw = full_qty * tp2_frac

        tp1_qty = round_qty(tp1_raw)
        tp2_qty = round_qty(tp2_raw)

        # Clip so TP1+TP2 never exceeds position size
        if tp1_qty + tp2_qty > full_qty:
            excess = tp1_qty + tp2_qty - full_qty
            tp2_qty = max(0.0, tp2_qty - excess)

        def valid_tp_qty(q: float, p: float) -> bool:
            if q <= 0:
                return False
            if q < min_qty:
                return False
            if q * p < min_notional:
                return False
            return True

        now_ms = int(time.time() * 1000)

        if not valid_tp_qty(tp1_qty, tp1_price):
            self._db.insert_lifecycle_event(
                now_ms,
                symbol,
                "tp1_qty_invalid",
                lc.phase.value,
                f"tp1_qty={tp1_qty}",
                config_id=self._config_id,
            )
            tp1_qty = 0.0

        if not valid_tp_qty(tp2_qty, tp2_price):
            self._db.insert_lifecycle_event(
                now_ms,
                symbol,
                "tp2_qty_invalid",
                lc.phase.value,
                f"tp2_qty={tp2_qty}",
                config_id=self._config_id,
            )
            tp2_qty = 0.0

        if tp1_qty == 0.0 and tp2_qty == 0.0:
            log.warning(f"No valid TP qty for {symbol}; runner only")
            return

        # Place orders
        if tp1_qty > 0.0:
            order1 = self._executor.place_reduce_only_tp(
                symbol=symbol,
                side=close_side,
                qty=tp1_qty,
                price=tp1_price,
                label="tp1",
            )
            if order1:
                lc.tp1_order_id = order1.get("orderId", "")
                lc.tp1_planned_qty = tp1_qty
                self._db.insert_lifecycle_event(
                    now_ms, symbol, "tp1_submitted", lc.phase.value
                )

        if tp2_qty > 0.0:
            order2 = self._executor.place_reduce_only_tp(
                symbol=symbol,
                side=close_side,
                qty=tp2_qty,
                price=tp2_price,
                label="tp2",
            )
            if order2:
                lc.tp2_order_id = order2.get("orderId", "")
                lc.tp2_planned_qty = tp2_qty
                self._db.insert_lifecycle_event(
                    now_ms, symbol, "tp2_submitted", lc.phase.value
                )

    def _handle_tp_execution(
        self,
        symbol: str,
        label: str,
        fill_qty: float,
        ts: int,
        side: str,
        data: dict,
    ) -> None:
        """Update lifecycle and local position for TP1/TP2 fills."""
        lc = self._lifecycle.get(symbol)
        if not lc:
            log.debug(f"TP exec for {symbol} with no lifecycle; ignoring")
            return

        pos = self._positions.get_position(symbol)
        if pos:
            if pos.side == "Buy" and side == "Sell":
                pos.size = max(0.0, pos.size - fill_qty)
            elif pos.side == "Sell" and side == "Buy":
                pos.size = max(0.0, pos.size - fill_qty)
            if pos.size == 0.0:
                self._positions.remove_position(symbol)

        event_name = ""
        if label == "tp1":
            lc.tp1_filled_qty += fill_qty
            lc.tp1_filled_ts = ts
            planned = lc.tp1_planned_qty or lc.tp1_filled_qty
            if planned > 0 and lc.tp1_filled_qty >= 0.99 * planned:
                lc.phase = LifecyclePhase.TP1_FILLED
                event_name = "tp1_fill_full"
            else:
                event_name = "tp1_fill_partial"
        elif label == "tp2":
            lc.tp2_filled_qty += fill_qty
            lc.tp2_filled_ts = ts
            planned = lc.tp2_planned_qty or lc.tp2_filled_qty
            if planned > 0 and lc.tp2_filled_qty >= 0.99 * planned:
                lc.phase = LifecyclePhase.TP2_FILLED
                event_name = "tp2_fill_full"
            else:
                event_name = "tp2_fill_partial"

        self._db.insert_lifecycle_event(ts, symbol, event_name, lc.phase.value, config_id=self._config_id)

        if self._db:
            try:
                order_id = data.get("orderId", "") or ""
                exec_id = data.get("execId", "") or data.get("executionId", "") or ""
                exec_price = float(data.get("execPrice", 0) or 0)
                realised_pnl = float(data.get("execPnl") or data.get("closedPnl") or 0)
                if order_id and exec_id:
                    self._db.insert_fill(ts, exec_id, order_id, symbol, side, fill_qty, exec_price, realised_pnl, config_id=getattr(self, "_config_id", None))
                self._db.insert_trade(ts, symbol, side, fill_qty, exec_price, order_id=order_id, order_link_id="", pnl=realised_pnl, config_id=getattr(self, "_config_id", None))
            except Exception as e:
                log.debug(f"TP fill insert_fill/insert_trade: {e}")

    def _boot(self) -> bool:
        """Boot: account, universe, sync, WS, recovery."""
        log.info("Boot: initializing")
        self._init_components()
        self._equity_usdt = self._fetch_equity()
        effective_equity = get_effective_equity_for_sizing(self.config, self.env, self._equity_usdt)
        self._risk.set_equity(effective_equity)
        self._risk.set_daily_start_pnl(self._equity_usdt)
        log.info(f"Equity: {self._equity_usdt:.2f} USDT" + (f" (sizing: {effective_equity:.2f} fixed)" if effective_equity != self._equity_usdt else ""))

        if self.config.exchange.one_way_mode:
            try:
                self._client.set_position_mode(mode=0)
            except Exception as e:
                log.warning(f"Set position mode: {e}")

        self._universe.refresh()
        if not self._universe.symbols:
            log.error("No symbols in universe")
            return False
        log.info(f"Universe: {len(self._universe.symbols)} symbols")

        try:
            pos_resp = self._client.get_positions(category="linear")
            pos_list = pos_resp.get("result", {}).get("list", [])
            linear_positions = [p for p in pos_list if float(p.get("size", 0) or 0) != 0]
            self._recon.sync_positions_from_rest(linear_positions)
            self._positions.sync_from_exchange([
                {"symbol": p.get("symbol"), "side": p.get("side", "Buy"), "size": p.get("size"),
                 "avgPrice": p.get("avgPrice"), "stopLoss": p.get("stopLoss"),
                 "takeProfit": p.get("takeProfit"), "updatedTime": p.get("updatedTime")}
                for p in linear_positions
            ])
        except Exception as e:
            log.warning(f"REST position sync: {e}")
            linear_positions = []

        if self.config.emergency_flatten_on_startup and linear_positions and not self.config.dry_run:
            log.warning("Emergency flatten on startup")
            self._executor.emergency_flatten(linear_positions)
            self._recon.sync_positions_from_rest([])
            self._positions.sync_from_exchange([])

        symbols = self._universe.symbols[:100]
        self._context.refresh_klines(symbols)
        self._context.refresh_funding(symbols)
        self._context.refresh_oi(symbols[:50])
        self._context.refresh_long_short_ratio(symbols[:50])

        if self.config.recover_orphan_positions:
            for prec in self._recon.get_open_positions():
                if self._lifecycle.get(prec.symbol) is None:
                    self._lifecycle.register(LifecycleState(
                        symbol=prec.symbol, side=prec.side, entry_price=prec.entry_price,
                        stop_loss=prec.stop_loss, take_profit=prec.take_profit, atr_at_entry=0,
                        size=prec.size, entry_ts=prec.updated_ts,
                    ))

        # Startup protection repair for positions without SL
        unprotected_found = False
        for prec in self._recon.get_open_positions():
            if prec.stop_loss <= 0:
                unprotected_found = True
                now_ms = int(time.time() * 1000)
                self._db.insert_lifecycle_event(
                    now_ms,
                    prec.symbol,
                    "protection_missing_detected",
                    "",
                    config_id=self._config_id,
                )
                if not self.config.repair_missing_protection_on_startup:
                    log.warning(
                        f"Protection missing for {prec.symbol} and repair disabled by config"
                    )
                    continue
                # Compute a conservative SL using ATR if available, else 1% of price
                state = self._market_state.get_state(prec.symbol)
                atr = None
                if state:
                    feat = self._feature_builder.build(state)
                    atr = feat.atr_14
                entry_price = prec.entry_price
                if not atr or atr <= 0:
                    atr = entry_price * 0.01
                sl_mult = self.config.stop_tp.atr_multiplier_sl
                if prec.side == "Buy":
                    raw_sl = entry_price - sl_mult * atr
                else:
                    raw_sl = entry_price + sl_mult * atr
                tick = self._universe.get_tick_size(prec.symbol) or 0.01

                def round_price(p: float) -> float:
                    return round(p / tick) * tick

                stop_loss = round_price(raw_sl)
                self._db.insert_lifecycle_event(
                    now_ms,
                    prec.symbol,
                    "protection_repair_submitted",
                    "",
                    config_id=self._config_id,
                )
                try:
                    ok = self._executor.set_tp_sl(prec.symbol, None, stop_loss)
                except Exception as e:
                    log.error(f"Protection repair error for {prec.symbol}: {e}")
                    ok = False
                if ok:
                    self._db.insert_lifecycle_event(
                        now_ms,
                        prec.symbol,
                        "protection_repair_success",
                        "",
                        config_id=self._config_id,
                    )
                else:
                    self._db.insert_lifecycle_event(
                        now_ms,
                        prec.symbol,
                        "protection_repair_failed",
                        "",
                        config_id=self._config_id,
                    )

        max_per = self.config.public_ws_max_symbols_per_connection
        syms_ws = self._universe.symbols[:200]
        self._ws_shards = PublicWSShardManager(
            symbols=syms_ws, max_symbols_per_connection=max_per,
            testnet=self._bybit_env_type == "testnet",
            on_trade=self._on_trade, on_ticker=self._on_ticker,
        )
        self._ws_shards.build_shards()
        self._ws_shards.start_all()

        if self._effective_api_key and not self.config.dry_run:
            try:
                self._client.start_private_ws(
                    on_order=self._on_order,
                    on_position=self._on_position,
                    on_execution=self._on_execution,
                )
            except Exception as e:
                log.warning(f"Private WS: {e}")

        self._startup_sync_done = True
        log.info("Boot complete")
        return True

    def _run_context_refresh(self) -> None:
        last_kline = last_oi = last_funding = last_ls = last_inst = 0.0
        while self.running:
            try:
                t = time.time()
                symbols = self._universe.symbols[:100]
                if t - last_kline >= self.config.kline_refresh_seconds:
                    self._context.refresh_klines(symbols)
                    last_kline = t
                if t - last_oi >= self.config.oi_refresh_seconds:
                    self._context.refresh_oi(symbols[:50])
                    last_oi = t
                if t - last_funding >= self.config.funding_refresh_seconds:
                    self._context.refresh_funding(symbols)
                    last_funding = t
                if t - last_ls >= self.config.long_short_ratio_refresh_seconds:
                    self._context.refresh_long_short_ratio(symbols[:50])
                    last_ls = t
                if t - last_inst >= self.config.instrument_refresh_seconds:
                    self._context.refresh_instruments(self._universe)
                    last_inst = t
                if self._health:
                    self._health.report_ok("context_refresh")
                if self._ws_shards:
                    now_ms = int(time.time() * 1000)
                    self._ws_shards.monitor_and_reconnect(
                        now_ms,
                        int(self.config.public_ws_stale_timeout_seconds * 1000),
                        self.config.shard_reconnect_backoff_seconds,
                    )
                    self._ws_shards.refresh_symbols(self._universe.symbols[:200])
                    if self._health:
                        self._health.report_ok("public_ws")
            except Exception as e:
                log.debug(f"Context refresh: {e}")
            time.sleep(min(self.config.kline_refresh_seconds, 30))

    def _run_rest_reconciliation(self) -> None:
        while self.running:
            time.sleep(self.config.rest_reconciliation_interval_seconds)
            try:
                pos_resp = self._client.get_positions(category="linear")
                pos_list = pos_resp.get("result", {}).get("list", [])
                linear = [p for p in pos_list if float(p.get("size", 0) or 0) != 0]
                self._recon.sync_positions_from_rest(linear)
                self._positions.sync_from_exchange([
                    {"symbol": p.get("symbol"), "side": p.get("side"), "size": p.get("size"),
                     "avgPrice": p.get("avgPrice"), "stopLoss": p.get("stopLoss"),
                     "takeProfit": p.get("takeProfit"), "updatedTime": p.get("updatedTime")}
                    for p in linear
                ])
            except Exception as e:
                log.debug(f"REST reconciliation: {e}")
            # After syncing, ensure TP orders exist for any tracked positions
            try:
                for prec in self._recon.get_open_positions():
                    self._ensure_tp_orders_for_position(prec)
            except Exception as e:
                log.debug(f"Ensure TP orders: {e}")
            if self._health:
                self._health.report_ok("reconciliation")
            if getattr(self.config, "burn_in", None) and getattr(self.config.burn_in, "burn_in_enabled", False):
                try:
                    run_protection_audit(
                        self._db,
                        self._recon.get_open_positions(),
                        self._lifecycle.get,
                        self.config,
                        config_id=self._config_id,
                        repair_missing=self.config.repair_missing_protection_on_startup,
                        executor_set_tp_sl=self._executor.set_tp_sl if not self.config.dry_run else None,
                    )
                except Exception as e:
                    log.debug(f"Protection audit: {e}")

    def _score_and_enter_loop(self) -> None:
        degradation_interval_sec = 300.0
        while self.running:
            now_ms = int(time.time() * 1000)
            now_sec = time.time()
            try:
                if now_sec - self._last_degradation_check_ts >= degradation_interval_sec:
                    self._last_degradation_check_ts = now_sec
                    try:
                        from src.promotion.live_monitor import LiveDegradationMonitor
                        mon = LiveDegradationMonitor(self.config.database_path)
                        from_ts = now_ms - 24 * 3600 * 1000
                        events, status = mon.check_from_db(from_ts=from_ts, to_ts=now_ms, config_id=self._config_id)
                        if status == "insufficient_data":
                            log.debug("Degradation check: insufficient data (need min trades)")
                            if self._health:
                                self._health.report_ok("degradation_monitor", "insufficient_data")
                        elif status == "degradation_detected" and events:
                            log.warning("Degradation check: {} event(s) persisted", len(events))
                            if self._health:
                                self._health.report_fail("degradation_monitor", f"{len(events)} events")
                        else:
                            if self._health:
                                self._health.report_ok("degradation_monitor")
                    except Exception as e:
                        log.debug("Degradation check: {}", e)
                        if self._health:
                            self._health.report_fail("degradation_monitor", str(e))

                if self._health:
                    self._health.report_ok("score_entry")
                if self._client and self._effective_api_key and self._health:
                    self._health.report_ok("private_ws")
                if self._heartbeat_path and self._health and (now_sec - self._last_heartbeat_write_ts >= 30.0):
                    self._last_heartbeat_write_ts = now_sec
                    self._health.set_meta("config_id", self._config_id)
                    self._health.set_meta("strategy", getattr(self.config, "active_strategy", "flow_impulse"))
                    from src.monitoring.heartbeat import write_heartbeat
                    write_heartbeat(self._health, self._heartbeat_path)

                if get_bybit_env(self.env) == "demo" and getattr(getattr(self.config, "demo_probation", None), "enabled", False):
                    try:
                        from src.demo_probation import run_probation_fail_fast_check
                        if run_probation_fail_fast_check(self.config.database_path, self.config):
                            if self._stop_on_probation_failure():
                                break
                    except Exception as e:
                        log.debug("probation fail-fast check: %s", e)

                symbols = self._universe.symbols
                if not symbols:
                    time.sleep(self.config.score_interval_seconds)
                    continue
                self._equity_usdt = self._fetch_equity()
                effective_equity = get_effective_equity_for_sizing(self.config, self.env, self._equity_usdt)
                self._risk.set_equity(effective_equity)
                ok, reason = self._risk.check_daily_drawdown(self._equity_usdt)
                if not ok:
                    log.error(f"Kill switch: {reason}")
                    self._db.insert_kill_switch(now_ms, reason)
                    try:
                        from src.demo_probation import run_probation_fail_fast_check
                        run_probation_fail_fast_check(self.config.database_path, self.config)
                    except Exception as e:
                        log.debug("probation fail-fast check: %s", e)
                    self._stop_on_probation_failure()
                    break
                ok, reason = self._risk.check_daily_realized_loss()
                if not ok:
                    log.error(f"Kill switch: {reason}")
                    self._db.insert_kill_switch(now_ms, reason)
                    try:
                        from src.demo_probation import run_probation_fail_fast_check
                        run_probation_fail_fast_check(self.config.database_path, self.config)
                    except Exception as e:
                        log.debug("probation fail-fast check: %s", e)
                    self._stop_on_probation_failure()
                    break

                features_list = []
                for sym in symbols:
                    state = self._market_state.get_state(sym)
                    if state and state.last_price > 0:
                        features_list.append(self._feature_builder.build(state))
                if not features_list:
                    time.sleep(self.config.score_interval_seconds)
                    continue

                stage4 = getattr(self.config, "stage4_enabled", False)
                regime_labels = {}
                threshold_profiles = {}
                symbol_to_cluster = {}
                if stage4:
                    atr_pct_list = [f.atr_14 / (f.last_price or 1) for f in features_list if f.last_price]
                    atr_50 = float(__import__("numpy").percentile(atr_pct_list, 50)) if atr_pct_list else 0.0
                    for f in features_list:
                        regime_labels[f.symbol] = classify_regime(f, atr_percentile_50=atr_50)
                    if self.config.entry.use_adaptive_thresholds:
                        threshold_profiles = compute_adaptive_thresholds(
                            features_list,
                            self.config.entry.long_threshold,
                            self.config.entry.short_threshold,
                            self.config.entry.max_spread_bps,
                        )
                    symbol_to_cluster = cluster_by_correlation_proxy(features_list, correlation_threshold=0.7)
                current_longs = [p.symbol for p in self._positions.get_all_positions() if p.side == "Buy"]
                current_shorts = [p.symbol for p in self._positions.get_all_positions() if p.side == "Sell"]

                signals = self._scorer.score_all(
                    features_list,
                    max_longs=self.config.risk.max_concurrent_positions,
                    max_shorts=self.config.risk.max_concurrent_positions,
                    stage4_enabled=stage4,
                    regime_labels=regime_labels if stage4 else None,
                    threshold_profiles=threshold_profiles if stage4 else None,
                    symbol_to_cluster=symbol_to_cluster if stage4 else None,
                    current_long_symbols=current_longs,
                    current_short_symbols=current_shorts,
                )
                for sig in signals:
                    json_feat = ""
                    if sig.raw_features or sig.score_components or sig.regime_label or sig.threshold_profile is not None or sig.cluster_id is not None:
                        import json
                        blob = {}
                        if sig.score_components:
                            blob["score_components"] = sig.score_components
                        if sig.regime_label:
                            blob["regime_label"] = sig.regime_label
                        if sig.threshold_profile:
                            blob["threshold_profile"] = sig.threshold_profile
                        if sig.cluster_id is not None:
                            blob["cluster_id"] = sig.cluster_id
                        if sig.rejection_reason:
                            blob["rejection_reason"] = sig.rejection_reason
                        json_feat = json.dumps(blob)
                    self._db.insert_signal(now_ms, sig.symbol, sig.score, sig.direction, sig.delta_1m, sig.buy_sell_ratio_1m, json_features=json_feat, config_id=self._config_id)

                long_count = sum(1 for p in self._positions.get_all_positions() if p.side == "Buy")
                short_count = sum(1 for p in self._positions.get_all_positions() if p.side == "Sell")
                total_notional = sum(abs(p.size * p.entry_price) for p in self._positions.get_all_positions())
                total_risk = sum(abs(p.size * (p.entry_price - p.stop_loss)) if p.stop_loss else 0 for p in self._positions.get_all_positions())
                stage5 = getattr(self.config, "stage5_enabled", False)
                budget_state = None
                if stage5:
                    positions_for_budget = [
                        (p.symbol, p.side, p.size, abs(p.entry_price - p.stop_loss) if p.stop_loss else 0)
                        for p in self._positions.get_all_positions()
                    ]
                    effective_equity = get_effective_equity_for_sizing(self.config, self.env, self._equity_usdt)
                    budget_state = build_budget_state(
                        effective_equity,
                        positions_for_budget,
                        symbol_to_cluster if stage4 else {},
                    )

                burn_in_block_entries = False
                burn_in = getattr(self.config, "burn_in", None)
                if burn_in and getattr(burn_in, "burn_in_enabled", False):
                    day_ago_ms = now_ms - 86400 * 1000
                    trades_24h = self._db.get_trades(since_ts=day_ago_ms, to_ts=now_ms, config_id=self._config_id)
                    trades_today = len(trades_24h)
                    notional_today = sum(abs(float(t.get("qty", 0) or 0) * float(t.get("price", 0) or 0)) for t in trades_24h)
                    prot = self._db.get_protection_audit(since_ts=day_ago_ms, to_ts=now_ms, config_id=self._config_id)
                    protection_mismatch_count = sum(1 for p in prot if not p.get("repaired"))
                    exec_audit = self._db.get_execution_audit(since_ts=day_ago_ms, to_ts=now_ms, config_id=self._config_id)
                    execution_drift_count = sum(1 for e in exec_audit if e.get("mismatch_reason"))
                    gate_result = check_burnin_gates(
                        self.config, self._db,
                        trades_today=trades_today,
                        notional_today_usdt=notional_today,
                        protection_mismatch_count=protection_mismatch_count,
                        execution_drift_count=execution_drift_count,
                        kill_switch_triggered=getattr(self._risk, "kill_switch_triggered", False),
                        config_id=self._config_id,
                    )
                    if gate_result.blocked_entries:
                        burn_in_block_entries = True
                        for b in gate_result.breaches:
                            log.warning("Burn-in gate: {}", b.get("message", b))

                refs: list[tuple[Any, Any]] = []
                candidates_for_alloc: list[CandidateForAllocation] = []
                if not burn_in_block_entries:
                    for sig in signals:
                        if sig.direction == "none":
                            if stage4 and getattr(sig, "rejection_reason", None):
                                self._db.insert_entry_decision(now_ms, sig.symbol, "none", f"rejected:stage4:{sig.rejection_reason}", sig.score, self.config.dry_run, config_id=self._config_id)
                            continue
                        eligible, rej = check_eligibility(sig.symbol, self._universe, self._context, self._positions, now_ms)
                        if not eligible:
                            self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:{rej}", sig.score, self.config.dry_run, config_id=self._config_id)
                            continue
                        if self._positions.get_position(sig.symbol):
                            continue
                        ok, rej = self._risk.can_open_position(
                            self._positions.count(), long_count, short_count,
                            sig.symbol, 0, total_notional, total_risk,
                        )
                        if not ok:
                            self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:{rej}", sig.score, self.config.dry_run, config_id=self._config_id)
                            continue
                        feat = sig.raw_features
                        if not feat:
                            continue
                        entry_price = feat.last_price
                        atr = feat.atr_14 or entry_price * 0.01
                        if sig.direction == "long":
                            stop = entry_price - self.config.stop_tp.atr_multiplier_sl * atr
                            side = "Buy"
                        else:
                            stop = entry_price + self.config.stop_tp.atr_multiplier_sl * atr
                            side = "Sell"
                        tick = self._universe.get_tick_size(sig.symbol)
                        if tick > 0:
                            stop = round(stop / tick) * tick
                        qty_step = self._universe.get_qty_step(sig.symbol)
                        min_qty = self._universe.get_min_qty(sig.symbol)
                        min_notional = self._universe.get_min_notional(sig.symbol)
                        max_notional_cap = self.config.risk.max_notional_per_symbol_usdt
                        avail = self._fetch_available_balance()
                        if avail is not None and avail > 0:
                            max_notional_cap = min(max_notional_cap, max(10.0, avail * 0.95))
                        sizing = self._risk.compute_position_size(
                            sig.symbol, side, entry_price, stop, qty_step, min_qty, min_notional,
                            max_notional_cap,
                        )
                        if sizing.reject_reason:
                            self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:sizing:{sizing.reject_reason}", sig.score, self.config.dry_run, config_id=self._config_id)
                            continue
                        refs.append((sig, feat))
                        candidates_for_alloc.append(CandidateForAllocation(
                            symbol=sig.symbol, side=side, score=sig.score, base_sizing=sizing,
                            cluster_id=getattr(sig, "cluster_id", None), entry_price=entry_price,
                            stop_price=stop, atr=atr, qty_step=qty_step, min_qty=min_qty,
                        ))

                    if stage5 and budget_state is not None and candidates_for_alloc:
                        decisions = allocate_candidate_set(
                            candidates_for_alloc,
                            budget_state,
                            self.config.risk,
                            self.config.risk.allocation_method,
                        )
                        for (sig, feat), (cand, dec) in zip(refs, decisions):
                            if dec.reject_reason:
                                self._db.insert_entry_decision(now_ms, cand.symbol, "long" if cand.side == "Buy" else "short", f"rejected:stage5:{dec.reject_reason}", cand.score, self.config.dry_run, config_id=self._config_id)
                                continue
                            long_count = sum(1 for p in self._positions.get_all_positions() if p.side == "Buy")
                            short_count = sum(1 for p in self._positions.get_all_positions() if p.side == "Sell")
                            ok, rej = self._risk.can_open_position(
                                self._positions.count(), long_count, short_count,
                                cand.symbol, 0,
                                sum(abs(p.size * p.entry_price) for p in self._positions.get_all_positions()),
                                sum(abs(p.size * (p.entry_price - p.stop_loss)) if p.stop_loss else 0 for p in self._positions.get_all_positions()),
                            )
                            if not ok:
                                self._db.insert_entry_decision(now_ms, cand.symbol, "long" if cand.side == "Buy" else "short", f"rejected:{rej}", cand.score, self.config.dry_run, config_id=self._config_id)
                                continue
                            final_qty = dec.qty
                            if self.config.dry_run:
                                reason = "DRY_RUN_ACCEPTED"
                                if dec.allocation_reason:
                                    reason += f":{dec.allocation_reason}"
                                if dec.resized:
                                    reason += ":resized"
                                self._db.insert_entry_decision(now_ms, cand.symbol, "long" if cand.side == "Buy" else "short", reason, cand.score, True, config_id=self._config_id)
                                log.info("Simulated entry (dry_run): {} {} qty={}", cand.symbol, cand.side, final_qty)
                                continue
                            try:
                                result = self._executor.place_entry(cand.symbol, cand.side, final_qty, None, cand.stop_price, None)
                                if result:
                                    self._risk.record_trade()
                                    self._positions.add_position(TrackedPosition(
                                        symbol=cand.symbol, side=cand.side, size=final_qty, entry_price=cand.entry_price,
                                        stop_loss=cand.stop_price, take_profit=0, entry_ts=now_ms, order_id=result.get("orderId", ""),
                                    ))
                                    self._lifecycle.register(LifecycleState(
                                        symbol=cand.symbol, side=cand.side, entry_price=cand.entry_price, stop_loss=cand.stop_price, take_profit=0,
                                        atr_at_entry=cand.atr, size=final_qty, entry_ts=now_ms, order_link_id=result.get("orderLinkId", ""),
                                    ))
                                    reason = "order_placed"
                                    if dec.allocation_reason:
                                        reason += f":{dec.allocation_reason}"
                                    if dec.resized:
                                        reason += ":resized"
                                    self._db.insert_entry_decision(now_ms, cand.symbol, "long" if cand.side == "Buy" else "short", reason, cand.score, False, config_id=self._config_id)
                                    log.info(f"Entry placed: {cand.symbol} {cand.side} qty={final_qty}" + (f" ({dec.allocation_reason})" if dec.allocation_reason else ""))
                                    record_entry_intent(
                                        self._db, now_ms, cand.symbol, cand.side, final_qty,
                                        cand.entry_price, cand.stop_price,
                                        result.get("orderId", ""), result.get("orderLinkId", ""),
                                        self._config_id, getattr(self.config, "active_strategy", "flow_impulse"),
                                    )
                                else:
                                    self._risk.record_api_error()
                                    self._db.insert_entry_decision(now_ms, cand.symbol, "long" if cand.side == "Buy" else "short", "order_failed", cand.score, False, config_id=self._config_id)
                            except Exception as e:
                                log.error(f"Entry error {cand.symbol}: {e}")
                                self._risk.record_api_error()
                                self._db.insert_entry_decision(now_ms, cand.symbol, "long" if cand.side == "Buy" else "short", str(e), cand.score, False, config_id=self._config_id)
                    else:
                        for sig in signals:
                            if sig.direction == "none":
                                continue
                            eligible, rej = check_eligibility(sig.symbol, self._universe, self._context, self._positions, now_ms)
                            if not eligible:
                                self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:{rej}", sig.score, self.config.dry_run, config_id=self._config_id)
                                continue
                            if self._positions.get_position(sig.symbol):
                                continue
                            ok, rej = self._risk.can_open_position(
                                self._positions.count(), long_count, short_count,
                                sig.symbol, 0, total_notional, total_risk,
                            )
                            if not ok:
                                self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:{rej}", sig.score, self.config.dry_run, config_id=self._config_id)
                                continue
                            feat = sig.raw_features
                            if not feat:
                                continue
                            entry_price = feat.last_price
                            atr = feat.atr_14 or entry_price * 0.01
                            if sig.direction == "long":
                                stop = entry_price - self.config.stop_tp.atr_multiplier_sl * atr
                                side = "Buy"
                            else:
                                stop = entry_price + self.config.stop_tp.atr_multiplier_sl * atr
                                side = "Sell"
                            tick = self._universe.get_tick_size(sig.symbol)
                            if tick > 0:
                                stop = round(stop / tick) * tick
                            qty_step = self._universe.get_qty_step(sig.symbol)
                            min_qty = self._universe.get_min_qty(sig.symbol)
                            min_notional = self._universe.get_min_notional(sig.symbol)
                            max_notional_cap = self.config.risk.max_notional_per_symbol_usdt
                            avail = self._fetch_available_balance()
                            if avail is not None and avail > 0:
                                max_notional_cap = min(max_notional_cap, max(10.0, avail * 0.95))
                            sizing = self._risk.compute_position_size(
                                sig.symbol, side, entry_price, stop, qty_step, min_qty, min_notional,
                                max_notional_cap,
                            )
                            if sizing.reject_reason:
                                self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:sizing:{sizing.reject_reason}", sig.score, self.config.dry_run, config_id=self._config_id)
                                continue
                            final_qty = sizing.qty
                            if stage5 and budget_state is not None:
                                alloc = allocate_risk(
                                    sizing, sig.symbol, side, sig.score,
                                    getattr(sig, "cluster_id", None), budget_state, self.config.risk,
                                    self.config.risk.allocation_method, sizing.risk_usdt,
                                )
                                if alloc.reject_reason:
                                    self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, f"rejected:stage5:{alloc.reject_reason}", sig.score, self.config.dry_run, config_id=self._config_id)
                                    continue
                                final_qty = alloc.qty
                                if qty_step > 0:
                                    final_qty = max(min_qty, round(final_qty / qty_step) * qty_step)
                            if self.config.dry_run:
                                self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, "DRY_RUN_ACCEPTED", sig.score, True, config_id=self._config_id)
                                log.info("Simulated entry (dry_run): {} {} qty={}", sig.symbol, side, final_qty)
                                continue
                            try:
                                result = self._executor.place_entry(sig.symbol, side, final_qty, None, stop, None)
                                if result:
                                    self._risk.record_trade()
                                    self._positions.add_position(TrackedPosition(
                                        symbol=sig.symbol, side=side, size=final_qty, entry_price=entry_price,
                                        stop_loss=stop, take_profit=0, entry_ts=now_ms, order_id=result.get("orderId", ""),
                                    ))
                                    self._lifecycle.register(LifecycleState(
                                        symbol=sig.symbol, side=side, entry_price=entry_price, stop_loss=stop, take_profit=0,
                                        atr_at_entry=atr, size=final_qty, entry_ts=now_ms, order_link_id=result.get("orderLinkId", ""),
                                    ))
                                    self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, "order_placed", sig.score, False, config_id=self._config_id)
                                    log.info(f"Entry placed: {sig.symbol} {side} qty={final_qty}")
                                    record_entry_intent(
                                        self._db, now_ms, sig.symbol, side, final_qty,
                                        entry_price, stop,
                                        result.get("orderId", ""), result.get("orderLinkId", ""),
                                        self._config_id, getattr(self.config, "active_strategy", "flow_impulse"),
                                    )
                                else:
                                    self._risk.record_api_error()
                                    self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, "order_failed", sig.score, False, config_id=self._config_id)
                            except Exception as e:
                                log.error(f"Entry error {sig.symbol}: {e}")
                                self._risk.record_api_error()
                                self._db.insert_entry_decision(now_ms, sig.symbol, sig.direction, str(e), sig.score, False, config_id=self._config_id)
            except Exception as e:
                log.error(f"Score/enter loop: {e}")
                self._db.insert_error(int(time.time() * 1000), "main", str(e))
            time.sleep(self.config.score_interval_seconds)

    def _lifecycle_loop(self) -> None:
        while self.running:
            try:
                now_ms = int(time.time() * 1000)
                for lc in self._lifecycle.all_open():
                    sym = lc.symbol
                    state = self._market_state.get_state(sym)
                    if not state:
                        continue
                    if self._lifecycle.should_move_to_breakeven(sym):
                        be = self._lifecycle.breakeven_price(sym)
                        if be is not None and self._executor.set_tp_sl(sym, None, be):
                            self._lifecycle.mark_stop_at_breakeven(sym)
                            self._db.insert_lifecycle_event(now_ms, sym, "stop_moved_breakeven", lc.phase.value, config_id=self._config_id)
                    if self._lifecycle.should_time_stop(sym, now_ms):
                        pos = self._positions.get_position(sym)
                        if pos and not self.config.dry_run:
                            side = "Sell" if pos.side == "Buy" else "Buy"
                            self._executor.close_position(sym, pos.size, side=side)
                        lc.exit_reason = "time_stop"
                        self._lifecycle.remove(sym)
                        self._positions.remove_position(sym)
                        self._db.insert_lifecycle_event(now_ms, sym, "time_stop", lc.phase.value, "max_hold", config_id=self._config_id)
                        continue
                    if self._lifecycle.should_flow_reversal_exit(sym, state.delta_1m):
                        pos = self._positions.get_position(sym)
                        if pos and not self.config.dry_run:
                            side = "Sell" if pos.side == "Buy" else "Buy"
                            self._executor.close_position(sym, pos.size, side=side)
                        lc.exit_reason = "flow_reversal"
                        self._lifecycle.remove(sym)
                        self._positions.remove_position(sym)
                        self._db.insert_lifecycle_event(now_ms, sym, "flow_reversal_exit", lc.phase.value, "flow_reversal", config_id=self._config_id)
                        continue
                    feat = self._feature_builder.build(state) if getattr(self, "_feature_builder", None) else None
                    if feat and getattr(self.config.stop_tp, "exhaustion_exit_enabled", False) and self._lifecycle.should_exhaustion_exit(sym, getattr(feat, "flow_exhaustion_score", 0), state.delta_1m, lc.side):
                        pos = self._positions.get_position(sym)
                        if pos and not self.config.dry_run:
                            side = "Sell" if pos.side == "Buy" else "Buy"
                            self._executor.close_position(sym, pos.size, side=side)
                        lc.exit_reason = "exhaustion"
                        self._lifecycle.remove(sym)
                        self._positions.remove_position(sym)
                        self._db.insert_lifecycle_event(now_ms, sym, "exhaustion_exit", lc.phase.value, "exhaustion", config_id=self._config_id)
                        continue
                    if feat and getattr(self.config.stop_tp, "failed_breakout_exit_enabled", False) and self._lifecycle.should_failed_breakout_exit(sym, getattr(feat, "failed_breakout_score", 0), getattr(feat, "price_return_1m", 0), lc.side):
                        pos = self._positions.get_position(sym)
                        if pos and not self.config.dry_run:
                            side = "Sell" if pos.side == "Buy" else "Buy"
                            self._executor.close_position(sym, pos.size, side=side)
                        lc.exit_reason = "failed_breakout"
                        self._lifecycle.remove(sym)
                        self._positions.remove_position(sym)
                        self._db.insert_lifecycle_event(now_ms, sym, "failed_breakout_exit", lc.phase.value, "failed_breakout", config_id=self._config_id)
                        continue
            except Exception as e:
                log.debug(f"Lifecycle loop: {e}")
            if self._health:
                self._health.report_ok("lifecycle")
            time.sleep(5)

    def run(self) -> Optional[int]:
        """Run the trading loop. Returns exit code 20 when Demo probation failed and auto_reinit_after_failure is True, else None (caller should exit 0)."""
        self.running = True
        self._reinit_requested = False
        if not self._boot():
            return None
        t_context = threading.Thread(target=self._run_context_refresh, daemon=True)
        t_context.start()
        t_recon = threading.Thread(target=self._run_rest_reconciliation, daemon=True)
        t_recon.start()
        t_lifecycle = threading.Thread(target=self._lifecycle_loop, daemon=True)
        t_lifecycle.start()
        try:
            self._score_and_enter_loop()
        finally:
            self.running = False
            if self._ws_shards:
                self._ws_shards.stop_all()
            self._client.stop_private_ws()
            self._client.stop_public_ws()
            self._db.insert_equity(int(time.time() * 1000), self._equity_usdt, 0, config_id=self._config_id)
            self._db.close()
            log.info("Shutdown complete")
        return EXIT_PROBATION_REINIT if getattr(self, "_reinit_requested", False) else None


@app.command()
def run(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Config file path (e.g. config/config.demo.yaml or config/config.live.yaml)"),
) -> None:
    """Run the trading bot."""
    config, env = load_config(config_path)
    log_file = config.logging.log_file
    if not log_file and getattr(config, "logs_dir", None):
        log_file = str(Path(config.logs_dir) / "bot.log")
    setup_logging(
        level=config.logging.level,
        log_file=log_file,
        rotation=config.logging.rotation,
        retention=config.logging.retention,
    )
    instance_tag = f" instance={config.instance_name}" if config.instance_name else ""
    logger.info("Starting bot mode={} dry_run={} env={}{}", config.mode, config.dry_run, get_bybit_env(env), instance_tag)
    if config.dry_run:
        logger.info("Execution: simulated only (no orders will be placed)")
    else:
        logger.info("Execution: real orders will be placed on {}", get_bybit_env(env).upper())
    bot = TradingBot(config, env)

    def shutdown(sig, frame):
        logger.info("Shutdown requested")
        bot.running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    exit_code = bot.run()
    if exit_code is not None:
        sys.exit(exit_code)


if __name__ == "__main__":
    app()
