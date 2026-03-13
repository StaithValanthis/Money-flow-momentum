"""Tests for Demo-only research verdict / strategy viability policy."""

from pathlib import Path

from src.config.config import Config, ResearchPolicyConfig
from src.research.verdict import (
    VERDICT_SEARCH_NOT_MATURE,
    VERDICT_DEMO_VALIDATION_NOT_MATURE,
    VERDICT_CANDIDATE_READY_FOR_REVIEW,
    VERDICT_STRATEGY_EDGE_DOUBTFUL,
    compute_research_verdict,
    write_research_verdict_artifact,
)


def _base_policy() -> ResearchPolicyConfig:
    return ResearchPolicyConfig(
        enabled=True,
        min_total_warm_start_candidates_before_strategy_judgment=500,
        min_real_demo_closed_trades_before_strategy_judgment=200,
        min_completed_eval_cycles_before_strategy_judgment=3,
        min_demo_profit_factor_for_candidate_review=1.10,
        min_demo_expectancy_for_candidate_review=0.0,
        max_demo_ultra_short_trade_fraction_for_candidate_review=0.25,
        require_no_surviving_candidate_after_thresholds=True,
        emit_verdict_in_status=True,
        emit_verdict_artifact=True,
    )


def _config_with_policy() -> Config:
    cfg = Config()
    cfg.operating_mode = "demo_research"
    cfg.research_policy = _base_policy()
    return cfg


def test_verdict_search_not_mature_when_warm_start_candidates_below_threshold() -> None:
    """SEARCH_NOT_MATURE when warm-start evidence is still below threshold."""
    cfg = _config_with_policy()
    evidence = {
        "total_warm_start_candidates": 100,  # below 500
        "total_demo_closed_trades": 0,
        "completed_eval_cycles": 0,
        "candidate_exists": False,
        "demo_profit_factor": 0.0,
        "demo_expectancy": 0.0,
        "demo_ultra_short_trade_fraction": 0.0,
    }
    verdict, reasons, _ = compute_research_verdict(cfg, evidence)
    assert verdict == VERDICT_SEARCH_NOT_MATURE
    assert "warm_start_candidates_below_threshold" in reasons


def test_verdict_demo_validation_not_mature_when_demo_evidence_low() -> None:
    """DEMO_VALIDATION_NOT_MATURE when search is mature but Demo validation is not."""
    cfg = _config_with_policy()
    evidence = {
        "total_warm_start_candidates": 1000,  # >=500
        "total_demo_closed_trades": 50,  # below 200
        "completed_eval_cycles": 1,  # below 3
        "candidate_exists": False,
        "demo_profit_factor": 0.0,
        "demo_expectancy": 0.0,
        "demo_ultra_short_trade_fraction": 0.0,
    }
    verdict, reasons, _ = compute_research_verdict(cfg, evidence)
    assert verdict == VERDICT_DEMO_VALIDATION_NOT_MATURE
    assert "demo_closed_trades_below_threshold" in reasons
    assert "completed_eval_cycles_below_threshold" in reasons


def test_verdict_candidate_ready_for_review_when_candidate_metrics_strong() -> None:
    """CANDIDATE_READY_FOR_REVIEW when a candidate exists and passes Demo thresholds."""
    cfg = _config_with_policy()
    evidence = {
        "total_warm_start_candidates": 1000,
        "total_demo_closed_trades": 300,
        "completed_eval_cycles": 5,
        "candidate_exists": True,
        "demo_profit_factor": 1.2,
        "demo_expectancy": 0.01,
        "demo_ultra_short_trade_fraction": 0.1,
    }
    verdict, reasons, _ = compute_research_verdict(cfg, evidence)
    assert verdict == VERDICT_CANDIDATE_READY_FOR_REVIEW
    assert "candidate_ready_for_review" in reasons


def test_verdict_strategy_edge_doubtful_when_thresholds_met_but_no_candidate() -> None:
    """STRATEGY_EDGE_DOUBTFUL when thresholds are met but no candidate ready and flag enabled."""
    cfg = _config_with_policy()
    evidence = {
        "total_warm_start_candidates": 2000,
        "total_demo_closed_trades": 1000,
        "completed_eval_cycles": 10,
        "candidate_exists": False,
        "demo_profit_factor": 0.9,
        "demo_expectancy": -0.01,
        "demo_ultra_short_trade_fraction": 0.3,
    }
    verdict, reasons, _ = compute_research_verdict(cfg, evidence)
    assert verdict == VERDICT_STRATEGY_EDGE_DOUBTFUL
    assert "no_candidate_with_required_pf_and_expectancy" in reasons


def test_write_research_verdict_artifact(tmp_path: Path) -> None:
    """Artifact writer produces research_verdict.json with verdict and evidence."""
    cfg = _config_with_policy()
    cfg.artifacts_root = str(tmp_path / "artifacts")
    verdict_result = {
        "verdict": VERDICT_DEMO_VALIDATION_NOT_MATURE,
        "reasons": ["demo_closed_trades_below_threshold"],
        "summary": {"total_warm_start_candidates": 0, "total_demo_closed_trades": 0, "completed_eval_cycles": 0},
        "evidence": {"total_warm_start_candidates": 0},
    }
    write_research_verdict_artifact(cfg, verdict_result)
    path = tmp_path / "artifacts" / "research" / "research_verdict.json"
    assert path.exists()
    import json

    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    assert blob.get("verdict") == VERDICT_DEMO_VALIDATION_NOT_MATURE
    assert blob.get("summary", {}).get("total_warm_start_candidates") == 0

