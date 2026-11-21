"""
engine/data/model.py

One row per finished match, with everything the trend engine needs.
Field names: <stat>_h = home team, <stat>_a = away team.

    ft = full-time goals     ht = half-time goals
    sh = shots               sot = shots on target
    co = corners             yc / rc = yellow / red cards
    of = offsides

None means "unknown", never zero - the trend engine must skip None.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

STAT_FIELDS = [
    "ft_h", "ft_a", "ht_h", "ht_a", "sh_h", "sh_a", "sot_h", "sot_a",
    "co_h", "co_a", "yc_h", "yc_a", "rc_h", "rc_a", "of_h", "of_a",
]
CSV_COLUMNS = ["league", "season", "date", "home", "away"] + STAT_FIELDS + [
    "espn_id", "sources", "espn_tried",
]


def season_of(d: date) -> int:
    """Start year of the season a date falls in (Jul-Jun seasons)."""
    return d.year if d.month >= 7 else d.year - 1


def _opt_int(v) -> Optional[int]:
    if v is None:
        return None
    v = str(v).strip()
    if v == "":
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


@dataclass
class Match:
    league: str
    season: int
    date: date
    home: str
    away: str
    ft_h: Optional[int] = None
    ft_a: Optional[int] = None
    ht_h: Optional[int] = None
    ht_a: Optional[int] = None
    sh_h: Optional[int] = None
    sh_a: Optional[int] = None
    sot_h: Optional[int] = None
    sot_a: Optional[int] = None
    co_h: Optional[int] = None
    co_a: Optional[int] = None
    yc_h: Optional[int] = None
    yc_a: Optional[int] = None
    rc_h: Optional[int] = None
    rc_a: Optional[int] = None
    of_h: Optional[int] = None
    of_a: Optional[int] = None
    espn_id: str = ""
    sources: str = ""
    espn_tried: int = 0

    @property
    def finished(self) -> bool:
        return self.ft_h is not None and self.ft_a is not None

    @property
    def needs_offsides(self) -> bool:
        return self.of_h is None or self.of_a is None

    def add_source(self, name: str) -> None:
        parts = [p for p in self.sources.split("+") if p]
        if name not in parts:
            parts.append(name)
        self.sources = "+".join(parts)

    def to_row(self) -> dict:
        row = {"league": self.league, "season": self.season,
               "date": self.date.isoformat(), "home": self.home, "away": self.away,
               "espn_id": self.espn_id, "sources": self.sources,
               "espn_tried": self.espn_tried}
        for f in STAT_FIELDS:
            v = getattr(self, f)
            row[f] = "" if v is None else v
        return row

    @classmethod
    def from_row(cls, row: dict) -> "Match":
        m = cls(
            league=row["league"], season=int(row["season"]),
            date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
            home=row["home"], away=row["away"],
            espn_id=row.get("espn_id", "") or "", sources=row.get("sources", "") or "",
            espn_tried=_opt_int(row.get("espn_tried")) or 0,
        )
        for f in STAT_FIELDS:
            setattr(m, f, _opt_int(row.get(f)))
        return m
