"""Burn-in / validation schema: execution audit, protection audit, gate breaches."""

BURNIN_SCHEMA = """
-- Execution audit: intended vs actual (entry intent -> order ack -> fill)
CREATE TABLE IF NOT EXISTS execution_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    intent_qty REAL NOT NULL,
    intent_price REAL,
    intent_stop REAL,
    order_id TEXT,
    order_link_id TEXT,
    ack_ts INTEGER,
    fill_qty REAL,
    fill_price REAL,
    fill_ts INTEGER,
    slippage_bps REAL,
    size_delta REAL,
    notional_delta REAL,
    mismatch_reason TEXT,
    config_id TEXT,
    strategy TEXT
);
CREATE INDEX IF NOT EXISTS idx_exec_audit_ts ON execution_audit(ts);
CREATE INDEX IF NOT EXISTS idx_exec_audit_order ON execution_audit(order_id);
CREATE INDEX IF NOT EXISTS idx_exec_audit_config ON execution_audit(config_id);

-- Protection audit: intended SL/TP/breakeven vs exchange state
CREATE TABLE IF NOT EXISTS protection_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    check_type TEXT NOT NULL,
    expected_value REAL,
    actual_value REAL,
    repaired INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    config_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_protection_audit_ts ON protection_audit(ts);
CREATE INDEX IF NOT EXISTS idx_protection_audit_config ON protection_audit(config_id);

-- Burn-in gate breaches
CREATE TABLE IF NOT EXISTS burnin_gate_breaches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    gate_name TEXT NOT NULL,
    value REAL,
    limit_value REAL,
    message TEXT,
    config_id TEXT,
    phase TEXT
);
CREATE INDEX IF NOT EXISTS idx_burnin_breaches_ts ON burnin_gate_breaches(ts);
"""
