"""Stage 3 schema: config versioning, evaluation, optimization, shadow, promotion, rollback."""

STAGE3_SCHEMA = """
-- Config version registry
CREATE TABLE IF NOT EXISTS config_versions (
    config_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    parent_config_id TEXT,
    status TEXT NOT NULL,
    description TEXT,
    config_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    artifact_path TEXT,
    FOREIGN KEY (parent_config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_config_versions_status ON config_versions(status);
CREATE INDEX IF NOT EXISTS idx_config_versions_created ON config_versions(created_at);

-- Config artifacts metadata (path on disk)
CREATE TABLE IF NOT EXISTS config_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(config_id),
    FOREIGN KEY (config_id) REFERENCES config_versions(config_id)
);

-- Evaluation runs
CREATE TABLE IF NOT EXISTS evaluation_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    config_id TEXT,
    from_ts INTEGER,
    to_ts INTEGER,
    created_at INTEGER NOT NULL,
    summary_json TEXT,
    report_path TEXT,
    FOREIGN KEY (config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_config ON evaluation_reports(config_id);
CREATE INDEX IF NOT EXISTS idx_eval_created ON evaluation_reports(created_at);

-- Optimization runs
CREATE TABLE IF NOT EXISTS optimization_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    baseline_config_id TEXT,
    created_at INTEGER NOT NULL,
    from_ts INTEGER,
    to_ts INTEGER,
    status TEXT NOT NULL,
    summary_json TEXT,
    FOREIGN KEY (baseline_config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_opt_run_created ON optimization_runs(created_at);

-- Per-segment / per-candidate results
CREATE TABLE IF NOT EXISTS optimization_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    config_id TEXT,
    segment_name TEXT,
    in_sample INTEGER,
    metrics_json TEXT,
    params_json TEXT,
    pass_fail INTEGER,
    reason_codes TEXT,
    FOREIGN KEY (run_id) REFERENCES optimization_runs(run_id),
    FOREIGN KEY (config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_opt_results_run ON optimization_results(run_id);

-- Walk-forward segments
CREATE TABLE IF NOT EXISTS walk_forward_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL,
    train_from INTEGER,
    train_to INTEGER,
    val_from INTEGER,
    val_to INTEGER,
    test_from INTEGER,
    test_to INTEGER,
    FOREIGN KEY (run_id) REFERENCES optimization_runs(run_id)
);

-- Candidate configs (from optimizer)
CREATE TABLE IF NOT EXISTS candidate_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id TEXT NOT NULL UNIQUE,
    optimizer_run_id TEXT,
    parent_config_id TEXT,
    windows_json TEXT,
    objective_summary TEXT,
    reason_codes TEXT,
    expected_improvements TEXT,
    caveats TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (config_id) REFERENCES config_versions(config_id),
    FOREIGN KEY (parent_config_id) REFERENCES config_versions(config_id)
);

-- Shadow runs (candidate running alongside baseline)
CREATE TABLE IF NOT EXISTS shadow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_config_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    stopped_at INTEGER,
    baseline_config_id TEXT,
    FOREIGN KEY (candidate_config_id) REFERENCES config_versions(config_id),
    FOREIGN KEY (baseline_config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_shadow_candidate ON shadow_runs(candidate_config_id);

-- Shadow decisions (what candidate would have done)
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_run_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    symbol TEXT,
    direction TEXT,
    reason TEXT,
    score REAL,
    baseline_decision TEXT,
    baseline_score REAL,
    FOREIGN KEY (shadow_run_id) REFERENCES shadow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_shadow_decisions_run ON shadow_decisions(shadow_run_id);
CREATE INDEX IF NOT EXISTS idx_shadow_decisions_ts ON shadow_decisions(ts);

-- Promotion events
CREATE TABLE IF NOT EXISTS promotion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promoted_config_id TEXT NOT NULL,
    previous_active_config_id TEXT,
    promoted_at INTEGER NOT NULL,
    reason TEXT,
    manual INTEGER NOT NULL,
    FOREIGN KEY (promoted_config_id) REFERENCES config_versions(config_id),
    FOREIGN KEY (previous_active_config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_promotion_ts ON promotion_events(promoted_at);

-- Rollback events
CREATE TABLE IF NOT EXISTS rollback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rolled_back_to_config_id TEXT NOT NULL,
    previous_active_config_id TEXT NOT NULL,
    rolled_back_at INTEGER NOT NULL,
    reason TEXT,
    FOREIGN KEY (rolled_back_to_config_id) REFERENCES config_versions(config_id),
    FOREIGN KEY (previous_active_config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_rollback_ts ON rollback_events(rolled_back_at);

-- Degradation events (live monitor)
CREATE TABLE IF NOT EXISTS degradation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    severity TEXT NOT NULL,
    metric TEXT,
    value REAL,
    threshold REAL,
    message TEXT,
    FOREIGN KEY (config_id) REFERENCES config_versions(config_id)
);
CREATE INDEX IF NOT EXISTS idx_degradation_config_ts ON degradation_events(config_id, ts);
"""

# Add config_id to existing tables via migrations (ALTER ADD COLUMN if not exists)
STAGE3_ALTERS = [
    "ALTER TABLE trades ADD COLUMN config_id TEXT;",
    "ALTER TABLE orders ADD COLUMN config_id TEXT;",
    "ALTER TABLE fills ADD COLUMN config_id TEXT;",
    "ALTER TABLE signal_snapshots ADD COLUMN config_id TEXT;",
    "ALTER TABLE entry_decisions ADD COLUMN config_id TEXT;",
    "ALTER TABLE lifecycle_events ADD COLUMN config_id TEXT;",
    "ALTER TABLE equity_curve ADD COLUMN config_id TEXT;",
]
