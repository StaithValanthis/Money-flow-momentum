# Install and Run Guide (Ubuntu)

Canonical install and run workflow for Money Flow Momentum on a headless Ubuntu CLI server. All commands assume you are at the **repo root** and (unless noted) have activated the venv: `source venv/bin/activate`.

---

## Prerequisites

- Ubuntu 20.04+ (or similar Linux)
- Python 3.11+ (`python3 --version`)
- Git (to clone repo)

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

- `mode: paper`
- Enable burn-in and set phase to `demo`:

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

Confirm `selected_environment: DEMO`, `burn_in_phase: demo`, `dry_run: false` (for real Demo orders), and `selected_key_pair: present`.

### 1.7 Optional: install systemd service

```bash
./scripts/install_systemd.sh
sudo systemctl enable money-flow-momentum
# Start later with: sudo systemctl start money-flow-momentum
```

User unit: `./scripts/install_systemd.sh --user` then `systemctl --user enable money-flow-momentum`.

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
| Promote candidate | `python run_bot.py promote --config-id <id>` |
| Promote status | `python run_bot.py promote status` |
| Rollback config | `python run_bot.py config rollback [--reason "reason"]` or `python run_bot.py rollback [--reason "reason"]` |
| Config list | `python run_bot.py config list` |

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

| Item | Path |
|------|------|
| Config | `config/config.yaml` |
| Env | `.env` |
| Database | `data/bot.db` (or `database_path` in config) |
| Logs | `logs/bot.log` |
| Heartbeat | `artifacts/heartbeat.json` |
| Burn-in readiness | `artifacts/burnin/readiness_*.json`, `readiness_*.md` |
| Environment promotion | `artifacts/validation/env_promotion_<ts>.json`, `env_promotion_<ts>.md` |
| Evaluations | `artifacts/evaluations/` |

---

## Systemd

| Action | Command |
|--------|---------|
| Install unit | `./scripts/install_systemd.sh` |
| Enable | `sudo systemctl enable money-flow-momentum` |
| Start | `sudo systemctl start money-flow-momentum` |
| Stop | `sudo systemctl stop money-flow-momentum` |
| Restart | `sudo systemctl restart money-flow-momentum` |
| Status | `./scripts/service_status.sh` or `sudo systemctl status money-flow-momentum` |
| Logs | `./scripts/tail_logs.sh` or `tail -f logs/bot.log` |

Service file: `money-flow-momentum.service` (installed to `/etc/systemd/system/` by install script).

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

- **Runtime mode:** `python run_bot.py show-runtime-mode` — shows `selected_environment` (DEMO/LIVE/TESTNET), `burn_in_phase`, `credential_mode`, `selected_key_pair`, `dual_key_configured`.
- **Status:** `python run_bot.py status` — shows active config, DB path, strategy, burn-in phase, heartbeat age.
- **Report:** `python run_bot.py report` — active config, degradation events, promotions, loop health.

---

See also: **docs/BURN_IN_OPERATOR_RUNBOOK.md**, **docs/BURN_IN_AND_LIVE_VALIDATION.md**, **docs/DEPLOYMENT_AND_HEALTHCHECKS.md**.
