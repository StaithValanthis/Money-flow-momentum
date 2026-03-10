"""Burn-in gate checks: enforce stricter limits when burn_in_enabled."""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config.config import BurnInConfig, Config
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class BurnInGateResult:
    passed: bool
    breaches: list[dict[str, Any]] = field(default_factory=list)
    blocked_entries: bool = False


def check_burnin_gates(
    config: Config,
    db,
    *,
    trades_today: int = 0,
    notional_today_usdt: float = 0.0,
    protection_mismatch_count: int = 0,
    execution_drift_count: int = 0,
    reconnect_count_last_hour: int = 0,
    kill_switch_triggered: bool = False,
    heartbeat_coverage: Optional[float] = None,
    config_id: Optional[str] = None,
) -> BurnInGateResult:
    """
    When burn_in_enabled, check gates. Returns result with breaches; blocked_entries=True
    if new entries should be blocked.
    """
    burn = getattr(config, "burn_in", None) or BurnInConfig()
    if not burn.burn_in_enabled:
        return BurnInGateResult(passed=True)

    breaches: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    phase = burn.burn_in_phase
    cid = config_id

    if trades_today >= burn.burn_in_max_trades_per_day:
        breaches.append({
            "gate": "burn_in_max_trades_per_day",
            "value": trades_today,
            "limit": burn.burn_in_max_trades_per_day,
            "message": f"Trades today {trades_today} >= limit {burn.burn_in_max_trades_per_day}",
        })
        if db:
            try:
                db.insert_burnin_gate_breach(now_ms, "burn_in_max_trades_per_day", float(trades_today), float(burn.burn_in_max_trades_per_day), breaches[-1]["message"], cid, phase)
            except Exception as e:
                log.debug(f"Insert burnin breach: {e}")

    if notional_today_usdt >= burn.burn_in_max_notional_usdt:
        breaches.append({
            "gate": "burn_in_max_notional_usdt",
            "value": notional_today_usdt,
            "limit": burn.burn_in_max_notional_usdt,
            "message": f"Notional today {notional_today_usdt:.0f} >= limit {burn.burn_in_max_notional_usdt:.0f}",
        })
        if db:
            try:
                db.insert_burnin_gate_breach(now_ms, "burn_in_max_notional_usdt", notional_today_usdt, burn.burn_in_max_notional_usdt, breaches[-1]["message"], cid, phase)
            except Exception as e:
                log.debug(f"Insert burnin breach: {e}")

    if burn.burn_in_fail_on_protection_mismatch and protection_mismatch_count > 0:
        breaches.append({
            "gate": "protection_mismatch",
            "value": protection_mismatch_count,
            "limit": 0,
            "message": f"Protection mismatches: {protection_mismatch_count}",
        })
        if db:
            try:
                db.insert_burnin_gate_breach(now_ms, "protection_mismatch", float(protection_mismatch_count), 0, breaches[-1]["message"], cid, phase)
            except Exception as e:
                log.debug(f"Insert burnin breach: {e}")

    if burn.burn_in_fail_on_execution_drift and execution_drift_count > 0:
        breaches.append({
            "gate": "execution_drift",
            "value": execution_drift_count,
            "limit": 0,
            "message": f"Execution drift count: {execution_drift_count}",
        })
        if db:
            try:
                db.insert_burnin_gate_breach(now_ms, "execution_drift", float(execution_drift_count), 0, breaches[-1]["message"], cid, phase)
            except Exception as e:
                log.debug(f"Insert burnin breach: {e}")

    if reconnect_count_last_hour > burn.burn_in_max_reconnect_per_hour:
        breaches.append({
            "gate": "burn_in_max_reconnect_per_hour",
            "value": reconnect_count_last_hour,
            "limit": burn.burn_in_max_reconnect_per_hour,
            "message": f"Reconnects in last hour {reconnect_count_last_hour} > {burn.burn_in_max_reconnect_per_hour}",
        })

    if kill_switch_triggered:
        breaches.append({"gate": "kill_switch", "value": 1, "limit": 0, "message": "Kill switch triggered"})

    if heartbeat_coverage is not None and heartbeat_coverage < burn.burn_in_min_expected_heartbeat_coverage:
        breaches.append({
            "gate": "heartbeat_coverage",
            "value": heartbeat_coverage,
            "limit": burn.burn_in_min_expected_heartbeat_coverage,
            "message": f"Heartbeat coverage {heartbeat_coverage:.2f} < {burn.burn_in_min_expected_heartbeat_coverage}",
        })

    blocked = len(breaches) > 0
    return BurnInGateResult(passed=not blocked, breaches=breaches, blocked_entries=blocked)
