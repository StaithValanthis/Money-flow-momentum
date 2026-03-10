# Burn-in and Live Validation

This document describes **burn-in mode** and how to use it for testnet and small-cap live validation.

## What is burn-in mode?

Burn-in is an **extra validation layer** on top of existing `dry_run` / `paper` / `live` modes. When enabled, it:

- Enforces **stricter limits** (max trades per day, max notional per day, reconnect tolerance, etc.).
- Writes **execution audit** records (intended vs actual size/price, slippage).
- Runs **protection-state audit** (intended SL/TP vs exchange state) and optionally repairs.
- **Blocks new entries** when gates are breached (and persists gate-breach events).
- Produces **readiness** classification and artifacts for operator review.

Burn-in does **not** change strategy logic or risk sizing formulas; it adds gates and observability.

## Config

In `config/config.yaml` (or your config file), under `burn_in`:

```yaml
burn_in:
  burn_in_enabled: true
  burn_in_phase: testnet          # testnet | live_small | live_guarded
  burn_in_max_trades_per_day: 20
  burn_in_max_notional_usdt: 5000
  burn_in_required_report_window_hours: 24
  burn_in_min_expected_heartbeat_coverage: 0.8
  burn_in_fail_on_protection_mismatch: true
  burn_in_fail_on_execution_drift: true
  burn_in_max_slippage_bps: 50
  burn_in_max_reconnect_per_hour: 5
```

- **burn_in_phase**: `testnet` for testnet continuation; `live_small` for small live; `live_guarded` for guarded live.
- **burn_in_fail_on_***: when true, any protection mismatch or execution drift in the window causes gate breach and blocks new entries until the next window or config change.
- Other limits are enforced only when burn-in is enabled.

## Running burn-in on testnet

1. Set exchange to testnet (e.g. `.env`: `BYBIT_TESTNET=true`).
2. Set `mode: paper` or `dry_run: true` if you want no real orders at first.
3. Enable burn-in and set a low `burn_in_max_trades_per_day` and `burn_in_max_notional_usdt`:

   ```yaml
   burn_in:
     burn_in_enabled: true
     burn_in_phase: testnet
     burn_in_max_trades_per_day: 10
     burn_in_max_notional_usdt: 2000
   ```

4. Run the bot:

   ```bash
   python3 run_bot.py run
   ```

5. Check status and readiness regularly:

   ```bash
   python3 run_bot.py status
   python3 run_bot.py burnin status
   python3 run_bot.py burnin readiness
   python3 run_bot.py burnin report
   ```

6. Inspect artifacts:

   - `artifacts/heartbeat.json` – loop freshness.
   - `artifacts/burnin/readiness_*.json` and `readiness_*.md` – after `burnin readiness` (or with `--output artifacts/burnin`).
   - DB tables: `execution_audit`, `protection_audit`, `burnin_gate_breaches`.

## Running burn-in on small live

1. After testnet looks good, set `mode: live` and use **small** size (e.g. low `risk_per_trade_pct`, low `burn_in_max_notional_usdt`).
2. Set:

   ```yaml
   burn_in:
     burn_in_enabled: true
     burn_in_phase: live_small
     burn_in_max_trades_per_day: 5
     burn_in_max_notional_usdt: 1000
   ```

3. Run and monitor the same way as testnet. Review evaluation reports for fill quality and execution drift before increasing size.

## Readiness

Readiness is computed from:

- Heartbeat coverage (if heartbeat file exists).
- Protection mismatch count (non-repaired) in the window.
- Execution drift count (audit rows with `mismatch_reason`) in the window.
- Burn-in gate breach count, kill-switch events, degradation events in the window.

**Classifications:**

- **NOT_READY** – e.g. kill switch triggered in window.
- **READY_FOR_TESTNET_CONTINUATION** – no breaches; OK to continue testnet.
- **READY_FOR_SMALL_LIVE** – no breaches; OK for small live (review metrics).
- **NEEDS_REVIEW** – protection mismatch, execution drift, gate breach, or degradation in window; operator should review before scaling up.

No automatic mode escalation; the operator decides when to change phase or limits.

## Metrics to review before increasing size

- **Execution audit**: `artifacts/` and DB `execution_audit` – intended vs actual size/price, slippage bps.
- **Fill quality** (evaluation report): avg/median entry slippage, execution drift count, median ack-to-fill delay.
- **Protection audit**: missing SL, breakeven not applied – ensure repairs or manual fix.
- **Gate breaches**: `burnin_gate_breaches` table or `burnin report` – resolve cause before raising limits.
- **Degradation events**: use existing degradation/promotion workflow.

## Failure conditions that block scale-up

When burn-in is enabled and any of these occur, **new entries are blocked** (existing positions remain managed):

- Trades today ≥ `burn_in_max_trades_per_day`.
- Notional today ≥ `burn_in_max_notional_usdt`.
- `burn_in_fail_on_protection_mismatch` true and protection mismatch count > 0 in window.
- `burn_in_fail_on_execution_drift` true and execution drift count > 0 in window.
- Kill switch triggered.
- Reconnects in last hour > `burn_in_max_reconnect_per_hour` (breach recorded).
- Heartbeat coverage below `burn_in_min_expected_heartbeat_coverage` (if passed in).

Blocked state is logged and written to `burnin_gate_breaches`; fix the cause and/or adjust config before continuing.

## What remains approximate or heuristic

- **Heartbeat coverage**: Simple age-based heuristic; no per-loop SLA.
- **Execution drift**: Based on `mismatch_reason` and size/notional delta from audit; partial fills may be aggregated per order.
- **Protection audit**: Compares lifecycle expected SL vs reconciled position; repair uses existing `set_tp_sl` (no TP structure change).
- **Readiness**: Classification is conservative and does not replace human judgment for go-live.

## CLI reference

```bash
# Burn-in status (config + recent gate breaches)
python3 run_bot.py burnin status

# Burn-in report (execution/protection/breaches in window)
python3 run_bot.py burnin report --window 24

# Readiness (classification + optional artifact write)
python3 run_bot.py burnin readiness
python3 run_bot.py burnin readiness --output artifacts/burnin --window 24

# General status/report (include burn-in line when enabled)
python3 run_bot.py status
python3 run_bot.py report
python3 run_bot.py health
```

See also **docs/DEPLOYMENT_AND_HEALTHCHECKS.md** and **docs/MONITORING_AND_ALERTING.md**.
