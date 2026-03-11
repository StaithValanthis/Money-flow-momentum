# Install and Run Guide (Ubuntu)

Canonical install and run workflow for Money Flow Momentum on a headless Ubuntu CLI server. All commands assume you are at the **repo root** and (unless noted) have activated the venv: `source venv/bin/activate`.

---

## Prerequisites

- Ubuntu 20.04+ (or similar Linux)
- Python 3.11+ (`python3 --version`)
- Git (to clone repo)

---

## Operating Modes

The bot has two top-level operating modes. Set **`operating_mode`** in `config/config.yaml`:

- **`demo_research`** – Autonomous Demo: trade, evaluate, optimize, shadow on Demo; no manual approval during research. Only safety issues block. Implies burn-in enabled, phase demo, automation enabled.
- **`live_guarded`** – Guarded Live: stricter limits; config promotion and Demo → Live require manual approval.

If omitted, mode is derived from env and automation/burn-in (Demo + automation + demo phase → `demo_research`; else `live_guarded`). Check: `python run_bot.py show-runtime-mode` or `python run_bot.py status` (both show `operating_mode`).

---

## Dual-instance (recommended)

You can run **Demo** and **Live** simultaneously on one host with full isolation: separate config, env, DB, artifacts, logs, and heartbeat per instance.

| Instance | Config | Env | DB | Artifacts | Logs |
|----------|--------|-----|-----|-----------|------|
| Demo | `config/config.demo.yaml` | `.env.demo` | `data/demo/bot.db` | `artifacts/demo/` | `logs/demo/` |
| Live | `config/config.live.yaml` | `.env.live` | `data/live/bot.db` | `artifacts/live/` | `logs/live/` |

- **Only the Demo instance** runs the automation timer (evaluate, optimize, shadow, recommendation). Live does not run automation.
- **Promotion remains manual:** operator promotes config and promote-env when appropriate; no auto-promote.
- **Demo auto-adopt (optional):** When `automation.auto_adopt_demo_candidates` is true in the Demo config, the Demo instance can automatically activate a better candidate as its **Demo** active config (never touches Live). The new config is used after the **next Demo restart**. Cross-instance promotion to Live still requires `promote-to-live` and/or `promote-env` with explicit operator action.
- **Fixed-equity Demo research (optional):** When `operating_mode: demo_research` and `demo_research.fixed_equity_enabled: true`, Demo uses a **fixed synthetic equity** (e.g. $1000) for position sizing and risk budgeting instead of the wallet balance, so research is not distorted by large Demo balances. `demo_research.relaxed_kill_switch_enabled` allows wider Demo-only kill-switch thresholds; `demo_research.demo_research_burnin_permissive` raises burn-in limits for long-running research. Live always uses actual equity and strict limits. Check with `python run_bot.py show-runtime-mode` (shows `fixed_equity_enabled`, `effective_strategy_equity_usdt`, `relaxed_kill_switch_enabled`).

**Validate per instance:**
```bash
python run_bot.py validate --config config/config.demo.yaml
python run_bot.py validate --config config/config.live.yaml
```

**Start (scripts):**
```bash
./scripts/start_demo_research.sh    # Demo research
./scripts/start_live_guarded.sh     # Live guarded
```

**Status / logs:**
```bash
./scripts/status_demo.sh
./scripts/status_live.sh
./scripts/tail_logs.sh demo [lines]
./scripts/tail_logs.sh live [lines]
./scripts/tail_logs.sh "demo automation" [lines]
./scripts/automation_status.sh   # point at demo (use with config/config.demo.yaml or run from repo where demo is default)
```

**Promote config / environment (manual):** When promoting from Demo to Live, use the **Live** config and **Live** env for the promote-env target; run promote-env with `--config config/config.live.yaml` and `--env .env.live` when applying the switch to the live instance’s env file.

```bash
python run_bot.py promote-env --config config/config.live.yaml --env .env.live
python run_bot.py promote-env --config config/config.live.yaml --env .env.live --confirm-live
```

**Systemd (dual-instance):** Use `./scripts/install_systemd.sh --dual-instance` to install `money-flow-momentum-demo.service`, `money-flow-momentum-live.service`, and `money-flow-momentum-demo-automation.service` + `.timer`. The script creates instance directories (`logs/demo`, `logs/live`, `data/demo`, `data/live`, `artifacts/demo`, `artifacts/live`) so the units can start. Start demo and live separately; only enable/start the demo-automation timer for the Demo instance.

---

## Part 1: Installation

### 1.1 Clone and enter repo

```bash
git clone <repo-url> Money-flow-momentum
cd Money-flow-momentum
```

### 1.2 Run installer

```bash
chmod +x install.sh
./install.sh
```

The installer:

- Checks Python version (warns if &lt; 3.11)
- Creates `venv` if missing
- Installs `requirements.txt`
- Creates `data/db`, `logs`, `artifacts`, `artifacts/burnin`, `artifacts/validation`
- Copies `config/config.yaml.example` → `config/config.yaml` if missing
- Warns if `.env` is missing

### 1.3 Bootstrap config and env

```bash
source venv/bin/activate
python bootstrap_config.py
```

- Prompts for **demo** API keys (Bybit Demo Trading; create from mainnet account → Demo Trading), optionally **live** keys.
- Writes `.env` with `BYBIT_ENV`, `BYBIT_DEMO_API_KEY/SECRET`, `BYBIT_LIVE_API_KEY/SECRET`.
- Creates or updates `config/config.yaml`. If you choose **demo**, the generated config has `mode: paper`, `dry_run: false`, `burn_in.burn_in_enabled: true`, and `burn_in.burn_in_phase: demo`, so **real Demo orders** are placed (no manual YAML edit needed for default demo path).

Or create `.env` manually from `.env.example` (set `BYBIT_ENV=demo`, demo and optionally live keys).

### 1.4 Edit config (optional)

Only needed if you want to change risk/limits or did not use bootstrap with demo. If you used bootstrap with **demo**, burn-in is already enabled.

To enable or adjust burn-in manually, edit `config/config.yaml`:

- Set **operating_mode** (recommended): `operating_mode: demo_research` for autonomous Demo (sets burn-in and automation automatically).
- Or set mode and burn-in explicitly: `mode: paper`, and:

```yaml
burn_in:
  burn_in_enabled: true
  burn_in_phase: demo
  burn_in_max_trades_per_day: 20
  burn_in_max_notional_usdt: 5000.0
```

### 1.5 Validate environment

```bash
source venv/bin/activate
python run_bot.py validate
```

Or: `./scripts/validate_env.sh`

Fix any reported errors before proceeding.

### 1.6 Inspect runtime mode

```bash
python run_bot.py show-runtime-mode
```

Or: `./scripts/show_runtime_mode.sh`

Confirm `operating_mode: demo_research` (or `selected_environment: DEMO`), `burn_in_phase: demo`, `dry_run: false` (for real Demo orders), and `selected_key_pair: present`.

### 1.7 Optional: install systemd service (main bot + automation timer)

```bash
./scripts/install_systemd.sh
sudo systemctl daemon-reload
sudo systemctl enable money-flow-momentum
# Start later with: sudo systemctl start money-flow-momentum

# Optional: enable Demo orchestration timer (runs automation cycle every 15 min)
sudo systemctl enable money-flow-momentum-automation.timer
sudo systemctl start money-flow-momentum-automation.timer
```

To install only the main bot (no automation timer): `./scripts/install_systemd.sh --no-automation`.

User unit: `./scripts/install_systemd.sh --user` then `systemctl --user enable money-flow-momentum` (and optionally enable/start the automation timer if installed).

---

## Part 2: Run Workflow

### Phase 1 — Demo burn-in

| Step | Command | Notes |
|------|---------|--------|
| Validate | `source venv/bin/activate && python run_bot.py validate` | Or `./scripts/validate_env.sh` |
| Runtime mode | `python run_bot.py show-runtime-mode` | Confirm DEMO, demo keys, phase demo |
| Start demo burn-in | `./scripts/start_testnet_burnin.sh` | Use `--foreground` to run in terminal |
| Monitor burn-in | `./scripts/check_burnin.sh` | Health, status, burnin status/report/readiness |
| Readiness | `python run_bot.py burnin readiness --output artifacts/burnin` | Produces readiness classification |

**First start (no systemd):**

```bash
source venv/bin/activate
./scripts/start_testnet_burnin.sh --foreground
```

**With systemd:**

```bash
sudo systemctl start money-flow-momentum
./scripts/check_burnin.sh
```

To run Demo orchestration (evaluation/optimizer/shadow/recommendation) periodically in the background, enable the automation timer after installing systemd: `sudo systemctl enable money-flow-momentum-automation.timer && sudo systemctl start money-flow-momentum-automation.timer`. The timer runs `python run_bot.py automation cycle` every 15 minutes; it does not start the trading bot and does not auto-promote config or environment. See **Automation timer** in the Systemd section below.

### Phase 2 — Promote environment (Demo → Live)

Only when readiness is **READY_FOR_SMALL_LIVE** and you have set live keys in `.env`.

| Step | Command | Notes |
|------|---------|--------|
| Preview | `python run_bot.py promote-env` | No changes; shows what would change |
| Confirm switch | `python run_bot.py promote-env --confirm-live` | Backs up .env and config, sets BYBIT_ENV=live, burn_in_phase=live_small |
| Optional reason | `python run_bot.py promote-env --confirm-live --reason "demo burn-in passed"` | |
| Inspect artifact | `cat artifacts/validation/env_promotion_<ts>.md` | Or list `artifacts/validation/env_promotion_*.json` |

Or use the shell wrapper: `./scripts/promote_demo_to_live.sh` (preview), `./scripts/promote_demo_to_live.sh --confirm-live`.

### Phase 3 — Guarded small-live

| Step | Command | Notes |
|------|---------|--------|
| Live readiness | `./scripts/check_small_live_ready.sh` | GO/NO-GO for small-live |
| Start guarded live | `./scripts/start_small_live.sh` | Use `--foreground` to run in terminal |
| Monitor | `./scripts/check_burnin.sh` | `python run_bot.py health` |

### Phase 4 — Adaptive workflow (evaluate / optimize / shadow / promote / rollback)

| Action | Command |
|--------|---------|
| Evaluation | `python run_bot.py evaluate --from-date YYYY-MM-DD --to-date YYYY-MM-DD` |
| Optimizer run | `python run_bot.py optimize run [--config-id <id>] [--from-date ...] [--to-date ...]` |
| Optimizer report | `python run_bot.py optimize report <run_id>` |
| Shadow start | `python run_bot.py shadow start --candidate-config-id <id>` |
| Shadow report | `python run_bot.py shadow report <candidate_config_id>` |
| Promote candidate (manual) | `python run_bot.py promote --config-id <id>` (within same instance) |
| **Import Demo candidate to Live** | `python run_bot.py promote-to-live --candidate-config-id <id> --demo-config config/config.demo.yaml --live-config config/config.live.yaml` (optional: `--activate`) |
| Promote status | `python run_bot.py promote status` |
| Rollback config | `python run_bot.py config rollback [--reason "reason"]` or `python run_bot.py rollback [--reason "reason"]` |
| Config list | `python run_bot.py config list` |
| **Demo automation cycle** (optional) | `python run_bot.py automation cycle` |
| **Demo automation status** | `python run_bot.py automation status` |

### Phase 5 — Incident response

| Action | Command |
|--------|---------|
| Stop service | `./scripts/incident_stop.sh` |
| Stop + rollback | `./scripts/incident_stop.sh --rollback "reason"` |
| Inspect logs | `tail -n 80 logs/bot.log` or `./scripts/tail_logs.sh 80` |
| Burn-in report | `python run_bot.py burnin report` |
| Restore env/config | Restore from `config/config.yaml.bak.<ts>` and `.env.bak.<ts>` if needed |

---

## Key file paths

| Item | Single-instance | Demo (dual) | Live (dual) |
|------|-----------------|-------------|-------------|
| Config | `config/config.yaml` | `config/config.demo.yaml` | `config/config.live.yaml` |
| Env | `.env` | `.env.demo` | `.env.live` |
| Database | `data/bot.db` | `data/demo/bot.db` | `data/live/bot.db` |
| Logs | `logs/bot.log` | `logs/demo/` | `logs/live/` |
| Heartbeat | `artifacts/heartbeat.json` | `artifacts/demo/heartbeat.json` | `artifacts/live/heartbeat.json` |
| Burn-in readiness | `artifacts/burnin/` | `artifacts/demo/burnin/` | `artifacts/live/burnin/` |
| Environment promotion | `artifacts/validation/` | `artifacts/demo/validation/` | `artifacts/live/validation/` |
| Evaluations | `artifacts/evaluations/` | `artifacts/demo/evaluations/` | `artifacts/live/evaluations/` |

---

## Systemd

Two units: **main trading bot** and **Demo orchestration timer**. Trading and orchestration are separate; the timer only runs `python run_bot.py automation cycle` and does not start the bot or auto-promote anything.

**Single-instance:** `./scripts/install_systemd.sh` (main + automation timer); use `--no-automation` to skip timer.

**Dual-instance:** `./scripts/install_systemd.sh --dual-instance` installs `money-flow-momentum-demo.service`, `money-flow-momentum-live.service`, and `money-flow-momentum-demo-automation.service` + `.timer`, and creates instance dirs (`logs/demo`, `logs/live`, etc.) so services can start. Only the Demo instance runs the automation timer; Live is trading-only.

| Action | Command |
|--------|---------|
| Install units | `./scripts/install_systemd.sh` (single) or `./scripts/install_systemd.sh --dual-instance` |
| Reload | `sudo systemctl daemon-reload` |
| **Main bot** enable/start (single) | `sudo systemctl enable money-flow-momentum` then `sudo systemctl start money-flow-momentum` |
| **Demo** (dual) | `sudo systemctl enable money-flow-momentum-demo` then `sudo systemctl start money-flow-momentum-demo` |
| **Live** (dual) | `sudo systemctl enable money-flow-momentum-live` then `sudo systemctl start money-flow-momentum-live` |
| **Automation timer** (single or dual; dual = demo only) | `sudo systemctl enable money-flow-momentum-automation.timer` or `money-flow-momentum-demo-automation.timer` then start |
| Status (single) | `./scripts/service_status.sh` |
| Status (dual) | `./scripts/status_demo.sh`, `./scripts/status_live.sh` |
| Logs main bot (single) | `./scripts/tail_logs.sh [lines]` or `tail -f logs/bot.log` |
| Logs (dual) | `./scripts/tail_logs.sh demo [lines]`, `./scripts/tail_logs.sh live [lines]` |
| Logs automation | `./scripts/tail_logs.sh automation [lines]` or `./scripts/tail_logs.sh "demo automation" [lines]` (dual) |
| Automation status + recommendation | `./scripts/automation_status.sh` |

Service files: `money-flow-momentum.service` (main bot), `money-flow-momentum-automation.service` + `money-flow-momentum-automation.timer` (orchestration). The automation timer is only meaningful when `automation.enabled` and Demo orchestration are enabled in config and the main bot is running in Demo.

---

## Quickstart (essential commands only)

```bash
# 1. Install
chmod +x install.sh && ./install.sh
source venv/bin/activate && python bootstrap_config.py

# 2. Validate
python run_bot.py validate
python run_bot.py show-runtime-mode

# 3. Start demo burn-in
./scripts/start_testnet_burnin.sh [--foreground]

# 4. Check burn-in
./scripts/check_burnin.sh

# 5. Preview promote-env
python run_bot.py promote-env

# 6. Confirm promote-env (when ready)
python run_bot.py promote-env --confirm-live

# 7. Start guarded live
./scripts/check_small_live_ready.sh
./scripts/start_small_live.sh [--foreground]

# 8. Evaluate
python run_bot.py evaluate [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD]

# 9. Optimize
python run_bot.py optimize run [--config-id <id>]
python run_bot.py optimize report <run_id>

# 10. Shadow report
python run_bot.py shadow report <candidate_config_id>

# 11. Promote candidate
python run_bot.py promote --config-id <id>

# 12. Rollback
python run_bot.py config rollback [--reason "reason"]
# or
python run_bot.py rollback [--reason "reason"]

# Incident stop
./scripts/incident_stop.sh [--rollback "reason"]
```

---

## Verifying current environment and phase

- **Runtime mode:** `python run_bot.py show-runtime-mode` — shows **operating_mode**, `selected_environment` (DEMO/LIVE/TESTNET), **automation_active**, **manual_approval_required**, `burn_in_phase`, `credential_mode`, `selected_key_pair`, `dual_key_configured`.
- **Status:** `python run_bot.py status` — shows operating_mode, active config, DB path, strategy, automation_active, burn-in phase, heartbeat age.
- **Report:** `python run_bot.py report` — active config, degradation events, promotions, loop health.

---

See also: **docs/BURN_IN_OPERATOR_RUNBOOK.md**, **docs/BURN_IN_AND_LIVE_VALIDATION.md**, **docs/DEPLOYMENT_AND_HEALTHCHECKS.md**.
