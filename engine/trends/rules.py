"""
engine/trends/rules.py

How one trend row is chosen: a FIXED-size sample, reported as "hits/games".

pick_window() takes a list of flags, NEWEST GAME FIRST:
    True  = the event happened in that game
    False = it did not
    None  = the stat is unknown for that game (skipped, never counted as a miss)
and returns the window to show, or None.

The windows are fixed (settings.FORM_WINDOWS, e.g. 8, 10, 12, 15, 20, 30 games), so
a trend is never "the stretch that happens to look best". Of the windows that fit
the available games, the longest one with a hit rate of at least GOOD_HIT_RATE
(70%) wins; if none reaches that, the longest one with at least MIN_HIT_RATE
(5/8) is used.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

from engine.trends import settings as S


@dataclass(frozen=True)
class Window:
    size: int        # games in the sample
    hits: int        # games where it happened
    skipped: int = 0     # games with unknown data that were skipped on the way

    @property
    def misses(self) -> int:
        return self.size - self.hits

    @property
    def rate(self) -> float:
        return self.hits / self.size


def pick_window(flags: List[Optional[bool]], windows: Sequence[int]) -> Optional[Window]:
    known = [f for f in flags if f is not None]
    good = floor = None
    for size in windows:
        if size > len(known):
            break
        hits = sum(known[:size])
        rate = hits / size
        if rate >= S.GOOD_HIT_RATE:
            good = Window(size, hits)
        if rate >= S.MIN_HIT_RATE:
            floor = Window(size, hits)
    return good or floor
