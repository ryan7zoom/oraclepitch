"""
engine/trends/fixtures.py

Upcoming fixtures from ESPN's scoreboard (one request per league per day, the
same endpoint the data layer already uses). ESPN team names are mapped to the
football-data.co.uk names used in data/matches/*.csv; a fixture with a team
that cannot be mapped is skipped and reported, never guessed.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set, Tuple

from engine.data.espn import BudgetExhausted
from engine.data.teams import TeamResolver
from engine.trends.engine import Fixture

logger = logging.getLogger("engine.trends.fixtures")


def upcoming_fixtures(espn, leagues, names_by_league: Dict[str, Set[str]], today, days: int,
                      now: datetime = None) -> Tuple[List[Fixture], List[dict]]:
    """Not-yet-started matches from `today` to `today + days`. Returns (fixtures, skipped)."""
    now = now or datetime.now(timezone.utc)
    fixtures, skipped, seen = [], [], set()
    for league in leagues:
        resolver = TeamResolver(league, names_by_league.get(league, set()))
        try:
            for offset in range(days + 1):
                for ev in espn.scoreboard(league, today + timedelta(days=offset)):
                    if ev.id in seen:
                        continue
                    seen.add(ev.id)
                    if ev.completed or ev.state in ("in", "post"):
                        continue
                    if ev.kickoff is not None and ev.kickoff < now:
                        continue
                    home, away = resolver.resolve(ev.home_name), resolver.resolve(ev.away_name)
                    if not (home and away):
                        skipped.append({"league": league, "home": ev.home_name, "away": ev.away_name,
                                        "reason": "team name not matched"})
                        continue
                    fixtures.append(Fixture(league, ev.date, home, away, ev.kickoff))
        except BudgetExhausted:
            logger.warning(f"[{league}] ESPN budget used up while listing fixtures")
            break
    fixtures.sort(key=lambda f: (f.kickoff or datetime.combine(f.date, datetime.min.time(), timezone.utc),
                                 f.league, f.home))
    return fixtures, skipped
