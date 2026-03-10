"""Tests for Stage 3: config versioning, evaluation, guardrails, promotion."""

import tempfile
import time
from pathlib import Path

import pytest

from src.config.config import Config
from src.config.versioning import (
    compute_config_hash,
    register_config_version,
    list_config_versions,
    get_active_config_id,
    activate_config_version,
    rollback_to_previous_config,
    get_config_version,
    diff_config_versions,
    ensure_stage3_schema,
)
from src.config.candidate_factory import generate_candidate, APPROVED_PARAM_PATHS
from src.evaluation.metrics import compute_core_metrics, compute_stratified_metrics, compute_diagnostic_metrics
from src.optimizer.walk_forward import WalkForwardSplitter, generate_segments
from src.optimizer.guardrails import check_guardrails, GuardrailResult, check_symbol_concentration
from src.optimizer.objectives import composite_objective
from src.promotion.rules import check_promotion_eligibility, PromotionRules


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


def test_config_hash_deterministic():
    c = Config()
    h1 = compute_config_hash(c)
    h2 = compute_config_hash(c)
    assert h1 == h2


def test_config_hash_changes_with_content():
    c1 = Config()
    c2 = Config()
    c2.entry.long_threshold = 2.0
    assert compute_config_hash(c1) != compute_config_hash(c2)


def test_register_and_list_config_versions(db_path):
    ensure_stage3_schema(db_path)
    c = Config()
    cid = register_config_version(
        c, version="v1", status="baseline", description="test", source="manual", db_path=db_path,
    )
    assert cid
    versions = list_config_versions(db_path=db_path)
    assert len(versions) >= 1
    assert any(v["config_id"] == cid for v in versions)


def test_activate_and_get_active(db_path):
    ensure_stage3_schema(db_path)
    c = Config()
    cid1 = register_config_version(c, version="v1", status="candidate", description="", source="manual", db_path=db_path)
    cid2 = register_config_version(c, version="v2", status="candidate", description="", source="manual", db_path=db_path)
    assert get_active_config_id(db_path) is None or get_active_config_id(db_path) != cid2
    ok = activate_config_version(cid2, db_path)
    assert ok
    assert get_active_config_id(db_path) == cid2


def test_rollback_to_previous(db_path):
    ensure_stage3_schema(db_path)
    c = Config()
    cid1 = register_config_version(c, version="v1", status="active", description="", source="manual", db_path=db_path)
    activate_config_version(cid1, db_path)
    c.entry.long_threshold = 1.8
    cid2 = register_config_version(c, version="v2", status="candidate", description="", source="manual", db_path=db_path)
    activate_config_version(cid2, db_path)
    assert get_active_config_id(db_path) == cid2
    prev = rollback_to_previous_config(db_path, reason="test rollback")
    assert prev == cid1
    assert get_active_config_id(db_path) == cid1


def test_candidate_generation(db_path):
    ensure_stage3_schema(db_path)
    c = Config()
    register_config_version(c, version="base", status="baseline", description="", source="manual", db_path=db_path)
    overrides = {"entry.long_threshold": 1.8, "entry.short_threshold": -1.8}
    cid = generate_candidate(c, overrides, version="cand1", db_path=db_path)
    assert cid
    rec = get_config_version(cid, db_path)
    assert rec and rec.get("status") == "candidate"


def test_core_metrics_empty():
    m = compute_core_metrics([])
    assert m["trade_count"] == 0
    assert m["total_pnl"] == 0.0


def test_core_metrics_with_trades():
    trades = [
        {"ts": 1000, "symbol": "BTCUSDT", "side": "Buy", "pnl": 10.0},
        {"ts": 2000, "symbol": "ETHUSDT", "side": "Sell", "pnl": -5.0},
    ]
    m = compute_core_metrics(trades)
    assert m["trade_count"] == 2
    assert m["total_pnl"] == 5.0
    assert m["win_rate"] == 0.5


def test_stratified_by_symbol():
    trades = [
        {"ts": 1000, "symbol": "BTCUSDT", "pnl": 10.0},
        {"ts": 2000, "symbol": "BTCUSDT", "pnl": 5.0},
        {"ts": 3000, "symbol": "ETHUSDT", "pnl": -3.0},
    ]
    by_sym = compute_stratified_metrics(trades, by="symbol")
    assert "BTCUSDT" in by_sym and "ETHUSDT" in by_sym
    assert by_sym["BTCUSDT"]["trade_count"] == 2
    assert by_sym["ETHUSDT"]["trade_count"] == 1


def test_walk_forward_segments():
    segs = generate_segments(1000, 10000, train_pct=0.5, val_pct=0.25, test_pct=0.25, n_splits=1)
    assert len(segs) == 1
    s = segs[0]
    assert s.train_from == 1000
    assert s.train_to <= s.val_from
    assert s.val_to < s.test_to
    assert s.test_to == 10000


def test_guardrails_low_trades():
    is_m = {"trade_count": 100, "return_pct": 5.0, "max_drawdown": 5.0}
    oos_m = {"trade_count": 5, "return_pct": 2.0, "max_drawdown": 8.0}
    r = check_guardrails(is_m, oos_m, min_trades=15)
    assert not r.passed
    assert "low_oos_trade_count" in r.reason_codes


def test_guardrails_passed():
    is_m = {"trade_count": 50, "return_pct": 3.0, "max_drawdown": 5.0}
    oos_m = {"trade_count": 30, "return_pct": 2.5, "max_drawdown": 6.0}
    r = check_guardrails(is_m, oos_m, min_trades=15)
    assert r.passed


def test_composite_objective():
    m = {"total_pnl": 100, "return_pct": 2.0, "max_drawdown": 5.0, "trade_count": 20, "sharpe_like": 0.5}
    s = composite_objective(m)
    assert isinstance(s, float)


def test_promotion_eligibility():
    cand = {"trade_count": 40, "return_pct": 1.0, "max_drawdown": 8.0}
    eligible, reasons = check_promotion_eligibility(cand, shadow_decision_count=100)
    assert eligible
    cand_low = {"trade_count": 10, "return_pct": 1.0, "max_drawdown": 8.0}
    eligible2, reasons2 = check_promotion_eligibility(cand_low, shadow_decision_count=100, rules=PromotionRules(min_trade_count=30))
    assert not eligible2
    assert "insufficient_trade_count" in reasons2
