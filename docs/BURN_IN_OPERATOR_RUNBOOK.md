# Burn-in Operator Runbook

Step-by-step operator workflow for Ubuntu CLI: install, validate, **demo burn-in** (Bybit Demo Trading), small-live readiness, guarded live start, and incident stop/rollback.

**Canonical reference:** For a single consolidated install and run sequence, see **[docs/INSTALL_AND_RUN_GUIDE.md](INSTALL_AND_RUN_GUIDE.md)**.

## Prerequisites

- Ubuntu 20.04+ (or similar Linux), headless CLI
- Python 3.11+ (`python3 --version`)
- Repo cloned and current directory = repo root for all commands below

---

## 1. Install on Ubuntu

```bash
chmod +x install.sh
./install.sh
```

Install script will:

- Check Python version (warns if &lt; 3.11)
- Create `venv` if missing
- Install `requirements.txt`
- Create `data/db`, `logs`, `artifacts`, `artifacts/burnin`, `artifacts/validation`
- Copy `config/config.yaml.example` → `config/config.yaml` if missing
- Warn if `.env` is missing

**Next steps printed at end:** edit config, run bootstrap if needed, validate, then run.

---

## 2. Bootstrap and config

- **API keys (paper/live):**  
  `python bootstrap_config.py`  
  Prompts for **demo** keys (Bybit Demo Trading; create from mainnet account → Demo Trading), optionally **live** keys (dual-key). Writes `BYBIT_ENV=demo|live`, `BYBIT_DEMO_API_KEY/SECRET`, `BYBIT_LIVE_API_KEY/SECRET`. When you choose **demo**, it also writes `config/config.yaml` with `mode: paper`, `dry_run: false`, `burn_in.burn_in_enabled: true`, and `burn_in.burn_in_phase: demo`, so real Demo orders are placed and no manual config edit is needed for the default demo path. Or create `.env` manually (see `.env.example`). Dual-key is recommended; legacy `BYBIT_API_KEY`/`BYBIT_API_SECRET` is supported as fallback. **Do not** use Demo Trading on Testnet; use official Bybit Demo (api-demo.bybit.com, stream-demo.bybit.com for private; public data from mainnet stream.bybit.com).

- **Config (only if you did not bootstrap with demo or want to change limits):**  
  Edit `config/config.yaml`: set `mode` (e.g. `paper` for demo), and optionally enable burn-in:

```yaml
burn_in:
  burn_in_enabled: true
  burn_in_phase: demo
  burn_in_max_trades_per_day: 20
  burn_in_max_notional_usdt: 5000.0
  # ... (see config/config.yaml.example)
```

---

## 3. Validate environment

Before first run or after config changes:

```bash
source venv/bin/activate
python run_bot.py validate
```

Or:

```bash
./scripts/validate_env.sh
```

Validation checks: config exists and loads, `.env` present for paper/live, DB and artifact dirs writable, mode/env consistency (BYBIT_ENV), active strategy in registry. Exits 1 on errors; warnings are printed but do not fail.

---

## 4. Systemd (optional)

```bash
./scripts/install_systemd.sh
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum
```

- **Status:** `./scripts/service_status.sh` or `sudo systemctl status money-flow-momentum`
- **Logs:** `./scripts/tail_logs.sh` or `tail -f logs/bot.log`
- User unit: `./scripts/install_systemd.sh --user` then `systemctl --user enable/start money-flow-momentum`

---

## 5. Demo burn-in start (recommended)

1. Set in config: `burn_in_enabled: true`, `burn_in_phase: demo`, `dry_run: false` (for real Demo orders; bootstrap sets this when you choose demo), and **demo** keys in `.env`: `BYBIT_ENV=demo`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` (create from mainnet account → Demo Trading; do not use testnet for demo).
2. Run:

```bash
./scripts/start_testnet_burnin.sh
```

With options:

- `--no-backup` — skip config backup before start
- `--foreground` — run bot in foreground instead of starting systemd

Script will: validate env, confirm burn-in phase=demo (or testnet for legacy) and BYBIT_ENV match, optionally back up config, start service (or run in foreground), then print monitor commands. **Demo** uses REST `https://api-demo.bybit.com`, private WS `wss://stream-demo.bybit.com`, and **mainnet public** market data `wss://stream.bybit.com` (per Bybit docs).

**Monitor commands (printed by script):**

```bash
./scripts/check_burnin.sh
./scripts/tail_logs.sh
python run_bot.py health
python run_bot.py burnin readiness --output artifacts/burnin
```

---

## 6. Burn-in check

Run regularly during demo (or testnet):

```bash
./scripts/check_burnin.sh
```

This runs: `health`, `status`, `burnin status`, `burnin report`, `burnin readiness` (writing to `artifacts/burnin`), then summarizes **healthy** / **needs review** / **blocked**. Exit code 2 if blocked.

**Manual equivalents:**

```bash
python run_bot.py health
python run_bot.py status
python run_bot.py burnin status
python run_bot.py burnin report
python run_bot.py burnin readiness --output artifacts/burnin
```

---

## 7. Readiness interpretation

- **NOT_READY** — e.g. kill switch in window; do not proceed.
- **READY_FOR_TESTNET_CONTINUATION** / **READY_FOR_DEMO_CONTINUATION** — OK to keep demo/testnet running.
- **READY_FOR_SMALL_LIVE** — No critical issues; operator may proceed to small-live **after** consciously switching phase and BYBIT_ENV=live (see below).
- **NEEDS_REVIEW** — Protection mismatch, execution drift, gate breach, or degradation; review artifacts and fix before scaling.

Readiness artifacts: `artifacts/burnin/readiness_*.json`, `readiness_*.md`.

---

## 8. Small-live readiness check (go/no-go)

**Do not** auto-switch phase. Operator must set `burn_in_phase: live_small` in config **and** `BYBIT_ENV=live` in `.env` when ready.

```bash
./scripts/check_small_live_ready.sh
```

Script checks:

- `burn_in_phase` is `live_small` (fails if not set by operator)
- `BYBIT_ENV=live` and live keys present
- Runs burn-in readiness and report
- Produces **GO** or **NO-GO** summary

If GO, proceed to guarded small-live start. If NO-GO, fix phase, readiness, or critical burn-in issues first.

---

## 8a. Promote environment (Demo -> Live) — safe helper

After demo burn-in and readiness passes, use the **promote-environment** helper to switch from Demo to Live. It does **not** auto-promote: you must run with `--confirm-live` to apply.

**Preview (default):** Shows current environment, readiness, live credentials, and what would change. No files are modified.

```bash
python run_bot.py promote-env
# or
./scripts/promote_demo_to_live.sh
```

**Apply the switch** (after readiness is READY_FOR_SMALL_LIVE and live keys are set):

```bash
python run_bot.py promote-env --confirm-live
# Optional reason:
python run_bot.py promote-env --confirm-live --reason "demo burn-in passed"
```

**Optional: apply and then start guarded live** (still requires you to run start script; the helper only prepares env):

```bash
python run_bot.py promote-env --confirm-live --start-live
```

**What the helper checks:**

- Current environment is Demo (`BYBIT_ENV=demo`).
- Burn-in readiness is **READY_FOR_SMALL_LIVE** (rejects NOT_READY, NEEDS_REVIEW).
- Live credentials exist (`BYBIT_LIVE_API_KEY` / `BYBIT_LIVE_API_SECRET` or legacy).
- Burn-in is enabled and phase is demo or testnet.

**What the helper changes (only when `--confirm-live`):**

- Backs up `.env` and `config/config.yaml` (unless `--no-backup`).
- Sets `BYBIT_ENV=live` in `.env`.
- Sets `burn_in_phase: live_small` in config.

**Artifact:** Each promotion is recorded under `artifacts/validation/env_promotion_<timestamp>.json` and `.md` (timestamp, previous/new env and phase, files changed, backups).

**Roll back manually:** Restore `.env` and config from the `.bak.<timestamp>` files created in the same directory, or set `BYBIT_ENV=demo` and `burn_in_phase: demo` again.

---

## 9. Guarded small-live start

1. In config: `burn_in_enabled: true`, `burn_in_phase: live_small`. In `.env`: `BYBIT_ENV=live`, `BYBIT_LIVE_API_KEY`, `BYBIT_LIVE_API_SECRET`.
2. Start:

```bash
./scripts/start_small_live.sh
```

Use `--foreground` to run in foreground instead of systemd.

Script validates env, burn-in phase and keys, then starts the service and prints post-start verification commands.

**Post-start verification:**

```bash
./scripts/check_burnin.sh
./scripts/tail_logs.sh 100
python run_bot.py health
```

---

## 10. Incident stop / rollback

**Safe stop (no rollback):**

```bash
./scripts/incident_stop.sh
```

This: stops the service, prints last 80 lines of `logs/bot.log`, latest burn-in readiness file from `artifacts/burnin`, and burn-in report. Does **not** flatten positions unless already configured elsewhere.

**Stop and rollback config:**

```bash
./scripts/incident_stop.sh --rollback "reason text"
```

Runs `python run_bot.py config rollback --reason "reason text"` after stop. Does not auto-flatten positions.

**Next steps (printed):** Review logs and artifacts; fix issues; then `./scripts/start_testnet_burnin.sh` (demo burn-in) or `./scripts/start_small_live.sh` as appropriate.

---

## 11. Where artifacts and logs live

| Item | Location |
|------|----------|
| Bot log (systemd) | `logs/bot.log` |
| Heartbeat | `artifacts/heartbeat.json` |
| Burn-in readiness | `artifacts/burnin/readiness_*.json`, `readiness_*.md` |
| **Environment promotion** | `artifacts/validation/env_promotion_<ts>.json`, `env_promotion_<ts>.md` |
| Burn-in bundle | `artifacts/burnin/bundle_<ts>/` (from `generate_burnin_bundle.sh`) |
| Config backups | `artifacts/validation/` or `artifacts/validation/backups/<ts>/` with `--timestamp` |
| DB | `data/bot.db` (or `database_path` in config) |

---

## 12. Systemd commands (reference)

```bash
./scripts/install_systemd.sh          # Install unit
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum
sudo systemctl stop money-flow-momentum
sudo systemctl restart money-flow-momentum
./scripts/service_status.sh
./scripts/tail_logs.sh [lines]
```

---

## 13. Operator menu and reporting

- **Command summary:**  
  `./scripts/operator_menu.sh`

- **Config backup:**  
  `./scripts/backup_config.sh` or `./scripts/backup_config.sh --timestamp`

- **Runtime mode (mode, phase, BYBIT_ENV, strategy):**  
  `./scripts/show_runtime_mode.sh` or `python run_bot.py show-runtime-mode`

- **Burn-in bundle (status, report, burnin status/report/readiness → one dir):**  
  `./scripts/generate_burnin_bundle.sh`  
  Optional: `./scripts/generate_burnin_bundle.sh artifacts/validation`

Can be run manually or from cron/systemd timer for periodic snapshots.

---

## 14. Evaluate, optimize, shadow, promote (after burn-in / live)

After demo or small-live run, use Stage 3 CLI for evaluation, optimization, shadow comparison, and promotion:

- **Evaluation** (writes to `artifacts/evaluations/`):  
  `python run_bot.py evaluate [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD] [--config-id <id>]`

- **Optimization:**  
  `python run_bot.py optimize run [--config-id <id>] [--from-date ...] [--to-date ...]`  
  `python run_bot.py optimize report <run_id>`

- **Shadow** (post-hoc comparison):  
  `python run_bot.py shadow start --candidate-config-id <id>`  
  `python run_bot.py shadow report <candidate_config_id>`

- **Promote / rollback:**  
  `python run_bot.py promote --config-id <id>`  
  `python run_bot.py promote status`  
  `python run_bot.py config rollback [--reason "reason"]`

See **docs/STAGE3_ADAPTIVE_FRAMEWORK.md** and **docs/OPTIMIZATION_WORKFLOW.md** for details. This flow is **operator-driven**; no automatic promotion.

---

## 15. Exact Ubuntu command cheat sheet

| Action | Command |
|--------|---------|
| Install | `./install.sh` |
| Validate | `source venv/bin/activate && python run_bot.py validate` or `./scripts/validate_env.sh` |
| Show runtime mode | `python run_bot.py show-runtime-mode` |
| **Preview promote Demo -> Live** | `python run_bot.py promote-env` or `./scripts/promote_demo_to_live.sh` |
| **Confirm promote Demo -> Live** | `python run_bot.py promote-env --confirm-live` |
| Start demo burn-in | `./scripts/start_testnet_burnin.sh` (requires BYBIT_ENV=demo, burn_in_phase: demo) |
| Check burn-in | `./scripts/check_burnin.sh` |
| Small-live readiness | `./scripts/check_small_live_ready.sh` |
| Start guarded live | `./scripts/start_small_live.sh` |
| Stop / inspect | `./scripts/incident_stop.sh` |
| Stop + rollback | `./scripts/incident_stop.sh --rollback "reason"` |
| Run evaluation | `python run_bot.py evaluate [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD]` |
| Run optimizer | `python run_bot.py optimize run [--config-id <id>]` |
| Shadow report | `python run_bot.py shadow report <candidate_config_id>` |
| Promote config | `python run_bot.py promote --config-id <id>` |
| Rollback config | `python run_bot.py config rollback [--reason "reason"]` |

See **docs/BURN_IN_AND_LIVE_VALIDATION.md** for burn-in semantics and **docs/DEPLOYMENT_AND_HEALTHCHECKS.md** for health/status/report details.
