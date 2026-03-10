"""Strategy registry: resolve active strategy by name."""

from typing import Optional

from src.config.config import Config
from src.strategies.base import BaseStrategy
from src.strategies.flow_impulse_strategy import FlowImpulseStrategy
from src.utils.logging import get_logger

log = get_logger(__name__)

_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy(name: str, strategy_class: type[BaseStrategy]) -> None:
    """Register a strategy class by name."""
    _REGISTRY[name] = strategy_class
    log.debug("Registered strategy: {}", name)


def get_strategy(name: str, config: Config) -> Optional[BaseStrategy]:
    """Instantiate and return strategy by name. Returns None if unknown."""
    if name not in _REGISTRY:
        return None
    return _REGISTRY[name](config)


def list_strategies() -> list[str]:
    """Return registered strategy names."""
    return list(_REGISTRY.keys())


# Register built-in strategies
register_strategy("flow_impulse", FlowImpulseStrategy)
