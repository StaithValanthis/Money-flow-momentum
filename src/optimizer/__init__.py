"""Optimizer: parameter space, walk-forward, objectives, guardrails."""

from src.optimizer.parameter_space import ParameterSpace, get_bounded_space
from src.optimizer.walk_forward import WalkForwardSplitter, generate_segments
from src.optimizer.objectives import composite_objective
from src.optimizer.guardrails import check_guardrails, GuardrailResult
from src.optimizer.candidate_selector import select_best_candidate

__all__ = [
    "ParameterSpace",
    "get_bounded_space",
    "WalkForwardSplitter",
    "generate_segments",
    "composite_objective",
    "check_guardrails",
    "GuardrailResult",
    "select_best_candidate",
]
