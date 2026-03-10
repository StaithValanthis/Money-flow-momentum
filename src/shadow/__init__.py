"""Shadow: run candidate alongside baseline, persist decisions, compare."""

from src.shadow.shadow_runner import ShadowRunner
from src.shadow.comparison import compare_baseline_shadow

__all__ = ["ShadowRunner", "compare_baseline_shadow"]
