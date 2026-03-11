# Burn-in Operator Runbook

Step-by-step operator workflow for Ubuntu CLI. The repo operates in two **canonical modes**:

- **`demo_research`** — Autonomous Demo research/tuning (BYBIT_ENV=demo, burn_in_phase: demo). Use for burn-in and optimization on Bybit Demo Trading.
- **`live_guarded`** — Guarded live trading (BYBIT_ENV=live). Stricter effective profile; manual approval required for config promotion and Demo→Live.

Set `operating_mode: demo_research` or `operating_mode: live_guarded` in config. Validation and scripts report readiness in terms of these modes.

**Dual-instance (recommended):** Run Demo and Live **simultaneously** on one host with full isolation. Use `config/config.demo.yaml` + `.env.demo` for the Demo instance and `config/config.live.yaml` + `.env.live` for the Live instance. Paths (DB, artifacts, logs, heartbeat) are then scoped per instance (`data/demo/bot.db`, `artifacts/demo/`, `logs/demo/` and `data/live/bot.db`, `artifacts/live/`, `logs/live/`). Only the Demo instance runs the automation timer; promotion remains manual. See [docs/INSTALL_AND_RUN_GUIDE.md](INSTALL_AND_RUN_GUIDE.md) for dual-instance commands.

Workflow: install → validate → **demo_research** (start Demo, check burn-in, readiness) → when ready, **promote environment** (Demo→Live) and **live_guarded** (check small-live readiness, start guarded live) → incident stop/rollback as needed.

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

Validation checks: config exists and loads, `.env` present for paper/live, DB and artifact dirs writable, mode/env consistency (BYBIT_ENV), active strategy in registry. On success, prints **operating_mode** and a mode-ready message (ready for **demo_research** or **live_guarded**). Exits 1 on errors; warnings are printed but do not fail.

---

## 4. Systemd (optional)

```bash
./scripts/install_systemd.sh
sudo systemctl daemon-reload
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum
```

Optional **Demo orchestration timer** (runs `python run_bot.py automation cycle` every 15 min; does not trade or auto-promote):

```bash
sudo systemctl enable money-flow-momentum-automation.timer
sudo systemctl start money-flow-momentum-automation.timer
```

- **Status:** `./scripts/service_status.sh` (both) or `./scripts/service_status.sh bot` / `./scripts/service_status.sh automation`
- **Logs:** `./scripts/tail_logs.sh` (main bot) or `./scripts/tail_logs.sh automation` (automation cycle)
- **Automation status:** `./scripts/automation_status.sh`
- User unit: `./scripts/install_systemd.sh --user` then `systemctl --user enable/start money-flow-momentum` (and optionally the automation timer)

---

## 5. Demo research start (demo_research)

1. Set in config: `operating_mode: demo_research` (or leave unset with burn_in_phase: demo), `burn_in_enabled: true`, `burn_in_phase: demo`, `dry_run: false` (for real Demo orders; bootstrap sets this when you choose demo), and **demo** keys in `.env`: `BYBIT_ENV=demo`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` (create from mainnet account → Demo Trading; do not use testnet for demo).
2. Run:

```bash
./scripts/start_testnet_burnin.sh
```

With options:

- `--no-backup` — skip config backup before start
- `--foreground` — run bot in foreground instead of starting systemd

Script will: validate env, confirm operating_mode and burn-in phase (demo or testnet) and BYBIT_ENV match, optionally back up config, start service (or run in foreground), then print monitor commands. **Demo** uses REST `https://api-demo.bybit.com`, private WS `wss://stream-demo.bybit.com`, and **mainnet public** market data `wss://stream.bybit.com` (per Bybit docs).

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

## 8. Guarded live (live_guarded) readiness check (go/no-go)

**Do not** auto-switch. Operator must set `operating_mode: live_guarded` in config (and optionally `burn_in_phase: live_small` or leave phase as `live_guarded`) **and** `BYBIT_ENV=live` in `.env` when ready.

```bash
./scripts/check_small_live_ready.sh
```

Script checks:

- `operating_mode` is `live_guarded` (fails if not set by operator)
- `BYBIT_ENV=live` and live keys present
- Runs burn-in readiness and report
- Produces **GO** or **NO-GO** summary

If GO, proceed to guarded live start. If NO-GO, fix operating_mode, phase, readiness, or critical burn-in issues first.

---

## 8a. Promote environment (demo_research → live_guarded) — safe helper

After demo run and readiness passes, use the **promote-environment** helper to switch from Demo to Live. It does **not** auto-promote: you must run with `--confirm-live` to apply.

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
- Sets `burn_in_phase: live_small` in config (and you should set `operating_mode: live_guarded` for mode-first operation).

**Artifact:** Each promotion is recorded under `artifacts/validation/env_promotion_<timestamp>.json` and `.md` (timestamp, previous/new env and phase, files changed, backups).

**Roll back manually:** Restore `.env` and config from the `.bak.<timestamp>` files created in the same directory, or set `BYBIT_ENV=demo` and `burn_in_phase: demo` again.

---

## 9. Guarded live start (live_guarded)

1. In config: `operating_mode: live_guarded`, `burn_in_enabled: true`, `burn_in_phase: live_guarded` or `live_small`. In `.env`: `BYBIT_ENV=live`, `BYBIT_LIVE_API_KEY`, `BYBIT_LIVE_API_SECRET`.
2. Start:

```bash
./scripts/start_small_live.sh
```

Use `--foreground` to run in foreground instead of systemd.

Script validates env, operating_mode=live_guarded, burn-in phase and keys, then starts the service and prints post-start verification commands.

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

| Item | Single | Demo (dual) | Live (dual) |
|------|--------|-------------|-------------|
| Bot log (systemd) | `logs/bot.log` | `logs/demo/` | `logs/live/` |
| Heartbeat | `artifacts/heartbeat.json` | `artifacts/demo/heartbeat.json` | `artifacts/live/heartbeat.json` |
| Burn-in readiness | `artifacts/burnin/` | `artifacts/demo/burnin/` | `artifacts/live/burnin/` |
| **Environment promotion** | `artifacts/validation/` | `artifacts/demo/validation/` | `artifacts/live/validation/` |
| Burn-in bundle | `artifacts/burnin/bundle_<ts>/` | `artifacts/demo/burnin/bundle_<ts>/` | — |
| Config backups | `artifacts/validation/` | `artifacts/demo/validation/` | `artifacts/live/validation/` |
| DB | `data/bot.db` | `data/demo/bot.db` | `data/live/bot.db` |

---

## 12. Systemd commands (reference)

**Main bot:**
```bash
./scripts/install_systemd.sh          # Install main + optional automation timer (use --no-automation to skip timer)
sudo systemctl daemon-reload
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum
sudo systemctl stop money-flow-momentum
sudo systemctl restart money-flow-momentum
```

**Automation timer** (Demo orchestration; runs `automation cycle` every 15 min; separate from trading):
```bash
sudo systemctl enable money-flow-momentum-automation.timer
sudo systemctl start money-flow-momentum-automation.timer
sudo systemctl stop money-flow-momentum-automation.timer
sudo systemctl disable money-flow-momentum-automation.timer
```

**Status and logs:**
```bash
./scripts/service_status.sh              # both
./scripts/service_status.sh bot           # main bot only
./scripts/service_status.sh automation    # automation timer + last run
./scripts/tail_logs.sh [lines]            # main bot log
./scripts/tail_logs.sh automation [lines] # automation cycle journal
./scripts/automation_status.sh            # automation status + recommendation
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
  `python run_bot.py promote --config-id <id>` (activate within same instance)  
  `python run_bot.py promote status`  
  **Cross-instance (Demo candidate → Live):**  
  `python run_bot.py promote-to-live --candidate-config-id <id> --demo-config config/config.demo.yaml --live-config config/config.live.yaml` (import only); add `--activate` to make it active in Live.  
  `python run_bot.py config show --config-id <live_id> --config config/config.live.yaml` (inspect imported config)  
  `python run_bot.py config rollback [--reason "reason"]`

- **Demo automation (optional):**  
  One-shot: `python run_bot.py automation cycle`  
  Status: `python run_bot.py automation status` or `./scripts/automation_status.sh`  
  For hands-off Demo orchestration, use the **automation timer**: `sudo systemctl enable money-flow-momentum-automation.timer && sudo systemctl start money-flow-momentum-automation.timer`. The timer runs `automation cycle` every 15 minutes; it does not start the trading bot and does not auto-promote config or environment. The main bot service must be running separately for trading.

---

## 15. Exact Ubuntu command cheat sheet

| Action | Command |
|--------|---------|
| Install | `./install.sh` |
| Validate (single) | `source venv/bin/activate && python run_bot.py validate` or `./scripts/validate_env.sh` |
| Validate (dual) | `python run_bot.py validate --config config/config.demo.yaml` / `--config config/config.live.yaml` |
| Show runtime mode | `python run_bot.py show-runtime-mode` (add `--config config/config.demo.yaml` or `config/config.live.yaml` for dual) |
| **Preview promote Demo → Live** | `python run_bot.py promote-env` or `./scripts/promote_demo_to_live.sh` (dual: `--config config/config.live.yaml --env .env.live`) |
| **Confirm promote Demo → Live** | `python run_bot.py promote-env --confirm-live` (dual: add `--config config/config.live.yaml --env .env.live`) |
| Start demo research | `./scripts/start_testnet_burnin.sh` or `./scripts/start_demo_research.sh` (dual) |
| Start guarded live | `./scripts/start_small_live.sh` or `./scripts/start_live_guarded.sh` (dual) |
| Check burn-in | `./scripts/check_burnin.sh` |
| Status (dual) | `./scripts/status_demo.sh`, `./scripts/status_live.sh` |
| Tail logs (dual) | `./scripts/tail_logs.sh demo [lines]`, `./scripts/tail_logs.sh live [lines]` |
| Guarded live readiness | `./scripts/check_small_live_ready.sh` (optional: config path for dual) |
| Stop / inspect | `./scripts/incident_stop.sh` |
| Stop + rollback | `./scripts/incident_stop.sh --rollback "reason"` |
| Run evaluation | `python run_bot.py evaluate [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD]` (use `--config` for dual) |
| Run optimizer | `python run_bot.py optimize run [--config-id <id>]` |
| Shadow report | `python run_bot.py shadow report <candidate_config_id>` |
| Promote config | `python run_bot.py promote --config-id <id>` (within instance) |
| **Import Demo candidate to Live** | `python run_bot.py promote-to-live --candidate-config-id <id> --demo-config config/config.demo.yaml --live-config config/config.live.yaml` |
| **Activate imported config in Live** | `python run_bot.py promote-to-live ... --activate` or `python run_bot.py promote --config-id <live_id> --config config/config.live.yaml` |
| Rollback config | `python run_bot.py config rollback [--reason "reason"]` |
| Enable automation timer | `sudo systemctl enable money-flow-momentum-automation.timer && sudo systemctl start ...` (single) or `money-flow-momentum-demo-automation.timer` (dual) |
| Disable automation timer | `sudo systemctl stop ... && sudo systemctl disable ...` |
| Automation status | `./scripts/automation_status.sh` or `python run_bot.py automation status` (point at demo when dual) |

See **docs/BURN_IN_AND_LIVE_VALIDATION.md** for burn-in semantics and **docs/DEPLOYMENT_AND_HEALTHCHECKS.md** for health/status/report details.
