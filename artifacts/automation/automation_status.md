# Demo Automation Status

- state: AWAITING_MANUAL_APPROVAL
- last_recommendation_status: READY_FOR_CONFIG_REVIEW
- active_config_id: None
- last_readiness_classification: READY_FOR_TESTNET_CONTINUATION
- last_evaluation_run_id: eval123
- last_optimizer_run_id: opt123
- best_candidate_config_id: candidate_xyz
- shadow_candidate_config_id: candidate_xyz

## Next manual commands

- python run_bot.py candidates list
- python run_bot.py shadow report --candidate-config-id candidate_xyz
- python run_bot.py promote --config-id candidate_xyz
- python run_bot.py promote-env
