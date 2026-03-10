# Burn-in and Live Validation

This document describes **burn-in mode** and how to use it for **Bybit Demo Trading** (recommended) and small-cap live validation.

**Canonical install/run:** See **docs/INSTALL_AND_RUN_GUIDE.md** for the exact Ubuntu install and run sequence.

## Credentials: dual-key (recommended)

Use **separate key pairs** for demo and live so you never overwrite one with the other:

- **Demo (Bybit Demo Trading):** `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` — create from your **mainnet** account → Demo Trading. **Do not** use testnet for demo; Bybit warns against "Demo Trading on Testnet."
- **Live:** `BYBIT_LIVE_API_KEY`, `BYBIT_LIVE_API_SECRET`
- **Selector:** `BYBIT_ENV=demo` → use demo keys; `BYBIT_ENV=live` → use live keys. Optional legacy: `BYBIT_ENV=testnet` uses testnet keys (legacy; prefer demo for burn-in).

**Endpoints for demo:** REST `https://api-demo.bybit.com`, private WS `wss://stream-demo.bybit.com`, **public market data** from mainnet `wss://stream.bybit.com` (demo public data is same as mainnet; use mainnet public per Bybit docs).

Run `python run_bot.py show-runtime-mode` to see `selected_environment` (DEMO/LIVE/TESTNET), `credential_mode` (dual_key vs legacy), and `selected_key_pair` (present/missing). The system **does not** auto-switch from demo to live; you must set `BYBIT_ENV=live` and have live keys before starting guarded small-live. Legacy single-key (`BYBIT_API_KEY`, `BYBIT_API_SECRET`) is supported as fallback but not recommended for production.

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
  burn_in_phase: demo             # demo | testnet | live_small | live_guarded
  burn_in_max_trades_per_day: 20
  burn_in_max_notional_usdt: 5000
  burn_in_required_report_window_hours: 24
  burn_in_min_expected_heartbeat_coverage: 0.8
  burn_in_fail_on_protection_mismatch: true
  burn_in_fail_on_execution_drift: true
  burn_in_max_slippage_bps: 50
  burn_in_max_reconnect_per_hour: 5
```

- **burn_in_phase**: `demo` for demo burn-in (recommended); `testnet` for legacy testnet; `live_small` for small live; `live_guarded` for guarded live.
- **burn_in_fail_on_***: when true, any protection mismatch or execution drift in the window causes gate breach and blocks new entries until the next window or config change.
- Other limits are enforced only when burn-in is enabled.

## Running burn-in on demo (recommended)

1. Set **demo** keys in `.env`: `BYBIT_ENV=demo`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` (create from mainnet account → Demo Trading; do not use testnet for demo).
2. Set `mode: paper` and `dry_run: false` to place **real Demo orders** (bootstrap does this when you choose demo). Set `dry_run: true` only if you want simulated entries with no orders.
3. Enable burn-in and set phase to `demo`:

   ```yaml
   burn_in:
     burn_in_enabled: true
     burn_in_phase: demo
     burn_in_max_trades_per_day: 10
     burn_in_max_notional_usdt: 2000
   ```

4. Run the bot: `python3 run_bot.py run` or `./scripts/start_testnet_burnin.sh`.
5. Check status and readiness regularly (see CLI reference below).
6. Inspect artifacts: `artifacts/heartbeat.json`, `artifacts/burnin/readiness_*.json`, DB tables.

## Running burn-in on testnet (legacy)

1. Set **testnet** keys: `BYBIT_ENV=testnet`, `BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET` (or legacy pair).
2. Set `burn_in_phase: testnet` and run as above. Prefer **demo** for new setups.

## Running burn-in on small live

1. After demo (or testnet) looks good, set **live** keys in `.env`: `BYBIT_ENV=live`, `BYBIT_LIVE_API_KEY`, `BYBIT_LIVE_API_SECRET` (or legacy pair), and set `mode: live` with **small** size.
2. Set `burn_in_phase: live_small` and run. Review evaluation reports for fill quality and execution drift before increasing size.

## Readiness

Readiness is computed from:

- Heartbeat coverage (if heartbeat file exists).
- Protection mismatch count (non-repaired) in the window.
- Execution drift count (audit rows with `mismatch_reason`) in the window.
- Burn-in gate breach count, kill-switch events, degradation events in the window.

**Classifications:**

- **NOT_READY** – e.g. kill switch triggered in window.
- **READY_FOR_TESTNET_CONTINUATION** / **READY_FOR_DEMO_CONTINUATION** – no breaches; OK to continue demo/testnet.
- **READY_FOR_SMALL_LIVE** – no breaches; OK for small live (review metrics).
- **NEEDS_REVIEW** – protection mismatch, execution drift, gate breach, or degradation in window; operator should review before scaling up.

No automatic mode escalation; the operator decides when to change phase or limits.

When `automation.enabled: true` and `automation.demo_orchestration_enabled: true` in `config/config.yaml`, the **Demo orchestration layer** will also compute readiness automatically on a conservative cadence (via `python run_bot.py automation cycle`) and use it to decide when to:

- run evaluation,
- run the optimizer,
- start/maintain shadow for the best candidate, and
- update a recommendation artifact under `artifacts/automation/`.

This automation is **Demo-only** and does **not** change burn-in semantics: it never auto-promotes config or environment; it only suggests next manual commands.

## Promote environment (Demo -> Live)

Use the **promote-env** helper to switch from Demo to guarded Live only when readiness passes and you explicitly confirm:

- **Preview:** `python run_bot.py promote-env` — shows current env, readiness, live credentials; no changes.
- **Apply:** `python run_bot.py promote-env --confirm-live` — backs up `.env` and config, sets `BYBIT_ENV=live` and `burn_in_phase: live_small`.
- **Optional:** `--reason "reason"`, `--start-live` (prints start command after switch).

The helper **does not** auto-promote: it requires `--confirm-live`. It checks readiness is READY_FOR_SMALL_LIVE and live credentials exist. Artifacts: `artifacts/validation/env_promotion_<ts>.json` and `.md`. Roll back by restoring the `.bak.<ts>` files or setting `BYBIT_ENV=demo` and `burn_in_phase: demo` again.

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

See also **docs/DEPLOYMENT_AND_HEALTHCHECKS.md**, **docs/MONITORING_AND_ALERTING.md**, and **docs/BURN_IN_OPERATOR_RUNBOOK.md** for the operator script workflow (install, validate, **demo** burn-in, small-live, incident stop, evaluate, optimize, shadow, promote/rollback).
