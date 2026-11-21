"""
engine/data/store.py

One CSV per league (data/matches/<league>.csv), committed to the repo.

Merge rules:
  * football-data.co.uk is authoritative for every stat it provides.
  * ESPN fills what is still unknown (always: offsides) and adds matches
    football-data.co.uk has not posted yet; when football-data.co.uk later
    posts that match its numbers replace ESPN's.
  * Matches are matched by (home, away) within +-1 day, so a UTC/local date
    difference never creates a duplicate.
"""

import csv
import os
from collections import defaultdict
from datetime import timedelta
from typing import Optional

from engine.data.model import CSV_COLUMNS, STAT_FIELDS, Match


class Store:
    def __init__(self, league: str, path: str):
        self.league = league
        self.path = path
        self.rows = []
        self._by_teams = defaultdict(list)

    def load(self) -> None:
        self.rows, self._by_teams = [], defaultdict(list)
        if not os.path.exists(self.path):
            return
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self._add(Match.from_row(row))

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            w.writeheader()
            for m in sorted(self.rows, key=lambda m: (m.date, m.home, m.away)):
                w.writerow(m.to_row())

    def _add(self, m: Match) -> None:
        self.rows.append(m)
        self._by_teams[(m.home, m.away)].append(m)

    def names(self) -> set:
        return {m.home for m in self.rows} | {m.away for m in self.rows}

    def find(self, home: str, away: str, d, tol_days: int = 1) -> Optional[Match]:
        for m in self._by_teams.get((home, away), []):
            if abs((m.date - d).days) <= tol_days:
                return m
        return None

    def upsert_fd(self, new: Match) -> None:
        cur = self.find(new.home, new.away, new.date)
        if cur is None:
            self._add(new)
            return
        cur.date, cur.season = new.date, new.season
        for f in STAT_FIELDS:
            v = getattr(new, f)
            if v is not None:
                setattr(cur, f, v)
        cur.add_source("fd")

    def fill_from_espn(self, cur: Match, stats: dict, ht=None) -> None:
        """Fill only unknown fields on an existing match."""
        for f, v in stats.items():
            if getattr(cur, f) is None:
                setattr(cur, f, v)
        if ht and cur.ht_h is None and cur.ht_a is None:
            cur.ht_h, cur.ht_a = ht
        cur.add_source("espn")

    def add_from_espn(self, new: Match) -> None:
        new.add_source("espn")
        self._add(new)
