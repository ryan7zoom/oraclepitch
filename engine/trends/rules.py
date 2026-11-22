"""
engine/trends/rules.py

The streak-selection rule (see settings.py for how it was inferred).

find_window() takes a list of flags, NEWEST GAME FIRST:
    True  = the event happened in that game
    False = it did not
    None  = the stat is unknown for that game (skipped, never counted as a miss)
and returns the longest window "N of last M" that passes, or None.
"""

from dataclasses import dataclass
from math import comb
from typing import List, Optional

from engine.trends import settings as S


@dataclass(frozen=True)
class Window:
    size: int        # M  in "N of last M"
    hits: int        # N
    skipped: int     # games with unknown data that were skipped inside the window

    @property
    def misses(self) -> int:
        return self.size - self.hits


def allowed_misses(size: int) -> int:
    for upto, misses in S.MISS_TIERS:
        if size <= upto:
            return misses
    return S.MISS_CAP


def passes(size: int, hits: int) -> bool:
    return size >= S.MIN_WINDOW and size - hits <= allowed_misses(size)


def find_window(flags: List[Optional[bool]]) -> Optional[Window]:
    size = hits = skipped = 0
    best = None
    for f in flags:
        if f is None:
            skipped += 1
            continue
        if size >= S.MAX_WINDOW:
            break
        size += 1
        hits += 1 if f else 0
        if f and passes(size, hits):          # oldest game of the window must be a hit
            best = Window(size, hits, skipped)
    return best


def tail_p(size: int, hits: int, base: Optional[float]) -> float:
    """Chance of >= `hits` hits in `size` games if the event happens with
    probability `base` every game (the league-wide rate). Small = unusual."""
    b = min(max(0.5 if base is None else base, 0.01), 0.99)
    return min(1.0, sum(comb(size, k) * b ** k * (1 - b) ** (size - k) for k in range(hits, size + 1)))
