"""Promotion: rules, promoter, live degradation monitor."""

from src.promotion.rules import PromotionRules, check_promotion_eligibility
from src.promotion.promoter import promote_candidate
from src.promotion.live_monitor import LiveDegradationMonitor

__all__ = ["PromotionRules", "check_promotion_eligibility", "promote_candidate", "LiveDegradationMonitor"]
