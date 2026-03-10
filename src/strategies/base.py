"""Strategy interface for multi-strategy readiness."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from src.data.feature_builder import SymbolFeatures
from src.data.market_state import SymbolState


@dataclass
class ScoredCandidate:
    """A single scored candidate (symbol + direction + score + metadata)."""

    symbol: str
    direction: str  # "long" | "short"
    score: float
    raw_features: Optional[SymbolFeatures] = None
    meta: Optional[dict[str, Any]] = None


class BaseStrategy(ABC):
    """Abstract strategy: build features, score candidates, evaluate entry, optional position management."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy identifier for config/versioning."""
        pass

    @abstractmethod
    def build_features(self, state: SymbolState) -> SymbolFeatures:
        """Build features from symbol state."""
        pass

    @abstractmethod
    def score_candidates(
        self,
        features_list: list[SymbolFeatures],
        max_longs: int = 5,
        max_shorts: int = 5,
        **kwargs: Any,
    ) -> list[ScoredCandidate]:
        """Score and rank candidates. Returns list of ScoredCandidate (direction long/short only)."""
        pass

    def evaluate_entry(self, candidate: ScoredCandidate, context: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Optional extra entry evaluation. Return (True, None) or (False, reason)."""
        return True, None

    def manage_position(self, symbol: str, side: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Optional position-level logic (e.g. adjust target). Return None or dict of suggestions."""
        return None
