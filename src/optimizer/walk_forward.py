"""Walk-forward train/validation/test splits."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Segment:
    train_from: Optional[int]
    train_to: Optional[int]
    val_from: Optional[int]
    val_to: Optional[int]
    test_from: Optional[int]
    test_to: Optional[int]
    index: int


class WalkForwardSplitter:
    """Splits a time range into train/val/test segments (rolling or expanding)."""

    def __init__(
        self,
        from_ts: int,
        to_ts: int,
        train_pct: float = 0.5,
        val_pct: float = 0.25,
        test_pct: float = 0.25,
        n_splits: int = 1,
    ):
        self.from_ts = from_ts
        self.to_ts = to_ts
        self.train_pct = train_pct
        self.val_pct = val_pct
        self.test_pct = test_pct
        self.n_splits = max(1, n_splits)

    def segments(self) -> list[Segment]:
        total = self.to_ts - self.from_ts
        if total <= 0:
            return []
        segs = []
        for i in range(self.n_splits):
            if self.n_splits == 1:
                train_end = self.from_ts + int(total * self.train_pct)
                val_end = train_end + int(total * self.val_pct)
                segs.append(Segment(
                    self.from_ts, train_end,
                    train_end, val_end,
                    val_end, self.to_ts,
                    i,
                ))
            else:
                step = total // self.n_splits
                seg_from = self.from_ts + i * step
                seg_to = self.from_ts + (i + 1) * step
                train_end = seg_from + int(step * self.train_pct)
                val_end = train_end + int(step * self.val_pct)
                segs.append(Segment(
                    seg_from, train_end,
                    train_end, val_end,
                    val_end, seg_to,
                    i,
                ))
        return segs


def generate_segments(
    from_ts: int,
    to_ts: int,
    train_pct: float = 0.5,
    val_pct: float = 0.25,
    test_pct: float = 0.25,
    n_splits: int = 1,
) -> list[Segment]:
    """Convenience: build splitter and return segments."""
    w = WalkForwardSplitter(from_ts, to_ts, train_pct, val_pct, test_pct, n_splits)
    return w.segments()
