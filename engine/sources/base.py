"""
engine/sources/base.py

Abstract interface that all data sources must implement. Keeping this
abstraction means the prediction engine never talks to a specific API
directly - it talks to this interface, so swapping or adding a data
source later (e.g. re-enabling a corners source) doesn't require
touching any model code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class MatchResult:
    """A single completed (or upcoming) match with whatever stats are available.

    Fields that a given source cannot provide should be left as None,
    not zero - zero and "unknown" are different things and models must
    not silently treat missing data as a real zero.
    """

    fixture_id: str
    date: date
    season: int
    home_team: str
    away_team: str
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    home_shots_on_target: Optional[int] = None
    away_shots_on_target: Optional[int] = None
    home_corners: Optional[int] = None
    away_corners: Optional[int] = None
    status: str = "unknown"  # "finished", "scheduled", "in_play", etc.
    raw: dict = field(default_factory=dict)  # original payload, for debugging

    @property
    def is_finished(self) -> bool:
        return self.status == "finished"

    @property
    def has_goals(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    @property
    def has_shots_on_target(self) -> bool:
        return (
            self.home_shots_on_target is not None
            and self.away_shots_on_target is not None
        )

    @property
    def has_corners(self) -> bool:
        return self.home_corners is not None and self.away_corners is not None


class DataSource(ABC):
    """Abstract data source. All methods return MatchResult objects (or lists
    thereof) so downstream code is source-agnostic.
    """

    @abstractmethod
    def get_fixtures(
        self, season: int, date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[MatchResult]:
        """Return fixtures (scheduled or finished) for a season, optionally
        filtered to a date range.
        """
        raise NotImplementedError

    @abstractmethod
    def get_fixture_statistics(self, fixture_id: str) -> MatchResult:
        """Return detailed statistics for a single fixture. Should raise
        on network/API failure rather than silently returning empty stats,
        so callers can decide how to handle a failed fetch (e.g. skip vs
        retry vs abort).
        """
        raise NotImplementedError

    @abstractmethod
    def get_todays_fixtures(self) -> list[MatchResult]:
        """Convenience method for the daily live-prediction workflow."""
        raise NotImplementedError
