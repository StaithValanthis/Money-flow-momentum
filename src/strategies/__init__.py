"""Strategy abstraction and registry."""

from src.strategies.base import BaseStrategy, ScoredCandidate
from src.strategies.registry import get_strategy, list_strategies, register_strategy

__all__ = ["BaseStrategy", "ScoredCandidate", "get_strategy", "list_strategies", "register_strategy"]
