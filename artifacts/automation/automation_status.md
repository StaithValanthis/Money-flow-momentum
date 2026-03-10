# Demo Automation Status

- state: AWAITING_MANUAL_APPROVAL
- last_recommendation_status: READY_FOR_CONFIG_REVIEW
- active_config_id: None
- last_readiness_classification: NEEDS_REVIEW
- last_evaluation_run_id: eval_ok
- last_optimizer_run_id: opt_ok
- best_candidate_config_id: cand_xyz
- shadow_candidate_config_id: cand_xyz

## Next manual commands

- python run_bot.py candidates list
- python run_bot.py shadow report --candidate-config-id cand_xyz
- python run_bot.py promote --config-id cand_xyz
- python run_bot.py promote-env
