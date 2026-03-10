# Promotion and Rollback

## Promotion Workflow

1. **Baseline active** – Current config is live.
2. **Optimizer proposes candidate** – Run `optimize run`; best candidate is registered with status `candidate`.
3. **Shadow (optional)** – Run shadow for the candidate to compare decisions vs baseline.
4. **Evaluation** – Run `evaluate` on the candidate’s window; ensure metrics meet thresholds.
5. **Stage** – Mark candidate as `staged` when ready for promotion.
6. **Promote** – Operator runs `promote --config-id <candidate>` (or auto-promotion if enabled and rules pass).
7. **Previous config** – The old active is retained for rollback.

## Promotion Rules

A candidate is **eligible** only if (configurable in `PromotionRules`):

- Minimum trade count (e.g. 30).
- Minimum shadow decision count if shadow was used (e.g. 50).
- Max drawdown not exceeded.
- Return above minimum.
- Optional: minimum improvement vs baseline.

**Default: manual promotion.** Auto-promotion is off unless explicitly enabled and configured conservatively.

## Rollback

- **One-command rollback**: `python3 run_bot.py rollback` or `python3 run_bot.py config rollback`
- Uses the **last promotion event** to determine the previous active config.
- Sets current active to `rolled_back`, previous to `active`.
- Persists a row in `rollback_events` with reason and timestamps.
- Next startup uses the rolled-back config.

## Flip-Flop Protection

- Rollback is explicit and persisted; rapid flip-flop is avoided by requiring a new promotion after rollback before the same config can be active again.
- No automatic re-promotion of the same config without going through the workflow again.

## CLI

```bash
# Promote candidate to active
python3 run_bot.py promote --config-id <config_id>

# Show current active config
python3 run_bot.py promote status

# Rollback to previous active
python3 run_bot.py rollback --reason "manual rollback"
```
