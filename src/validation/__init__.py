"""Burn-in and live validation."""

from src.validation.burn_in import check_burnin_gates, BurnInGateResult
from src.validation.readiness import compute_readiness, ReadinessResult

__all__ = [
    "check_burnin_gates",
    "BurnInGateResult",
    "compute_readiness",
    "ReadinessResult",
]
