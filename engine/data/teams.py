"""
engine/data/teams.py

Maps ESPN team names to the canonical names used by football-data.co.uk
(which is also what the rest of OraclePitch uses: "Man United", "Celta",
"Paris SG", ...).

Resolution order for one ESPN name:
  1. explicit alias table (names that cannot be matched by rule)
  2. the project's existing openfootball name table
  3. exact match after normalisation (accents, case, punctuation)
  4. canonical name's words all appear in the ESPN name
     ("Newcastle" is inside "Newcastle United")
  5. close fuzzy match, only if clearly better than the runner-up

The canonical list is whatever names football-data.co.uk has for that league,
so a name that cannot be resolved is NEVER invented: it is returned as None
and listed in the run report, so it can be added to ALIASES.
"""

import difflib
import re
import unicodedata
from typing import Optional

try:                                  # optional: older name table, if that file still exists
    from engine.sources.openfootball_source import _TEAM_NAME_MAPPINGS
except ImportError:
    _TEAM_NAME_MAPPINGS = {}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# key = norm(ESPN name), value = canonical (football-data.co.uk) name
ALIASES = {
    "epl": {
        "manchester city": "Man City",
        "manchester united": "Man United",
        "nottingham forest": "Nott'm Forest",
        "wolverhampton wanderers": "Wolves",
        "west bromwich albion": "West Brom",
        "tottenham hotspur": "Tottenham",
        "brighton and hove albion": "Brighton",
    },
    "la_liga": {
        "atletico madrid": "Ath Madrid",
        "athletic club": "Ath Bilbao",
        "athletic bilbao": "Ath Bilbao",
        "celta vigo": "Celta",
        "real sociedad": "Sociedad",
        "rayo vallecano": "Vallecano",
        "real betis": "Betis",
        "espanyol": "Espanol",
        "sporting gijon": "Sp Gijon",
        "deportivo alaves": "Alaves",
        "deportivo": "La Coruna",
        "deportivo la coruna": "La Coruna",
    },
    "bundesliga": {
        "eintracht frankfurt": "Ein Frankfurt",
        "borussia monchengladbach": "M'gladbach",
        "bayern munich": "Bayern Munich",
        "fc bayern munchen": "Bayern Munich",
        "1 fc koln": "FC Koln",
        "koln": "FC Koln",
        "fc cologne": "FC Koln",
        "cologne": "FC Koln",
        "fc st pauli": "St Pauli",
        "bayer leverkusen": "Leverkusen",
        "bayer 04 leverkusen": "Leverkusen",
    },
    "serie_a": {
        "inter milan": "Inter",
        "internazionale": "Inter",
        "fc internazionale milano": "Inter",
        "ac milan": "Milan",
        "hellas verona": "Verona",
    },
    "ligue_1": {
        "paris saint germain": "Paris SG",
        "olympique lyonnais": "Lyon",
        "olympique marseille": "Marseille",
        "stade rennais": "Rennes",
        "stade brestois": "Brest",
        "saint etienne": "St Etienne",
        "as saint etienne": "St Etienne",
    },
}


class TeamResolver:
    def __init__(self, league: str, canonical_names):
        self.league = league
        self.canon = set(canonical_names)
        self._norm = {c: norm(c) for c in self.canon}
        self._cache = {}
        self.unmatched = set()

    def resolve(self, name: str) -> Optional[str]:
        if name not in self._cache:
            result = self._resolve(name)
            self._cache[name] = result
            if result is None:
                self.unmatched.add(name)
        return self._cache[name]

    def _resolve(self, name: str) -> Optional[str]:
        n = norm(name)
        alias = ALIASES.get(self.league, {}).get(n)
        if alias in self.canon:
            return alias
        legacy = _TEAM_NAME_MAPPINGS.get(self.league, {}).get(name)
        if legacy in self.canon:
            return legacy
        for c, cn in self._norm.items():
            if cn == n:
                return c
        tokens = set(n.split())
        inside = [c for c, cn in self._norm.items() if cn and set(cn.split()) <= tokens]
        if len(inside) == 1:
            return inside[0]
        if len(inside) > 1:
            best = max(len(self._norm[c].split()) for c in inside)
            top = [c for c in inside if len(self._norm[c].split()) == best]
            if len(top) == 1:
                return top[0]
        scored = sorted(
            ((difflib.SequenceMatcher(None, n, cn).ratio(), c) for c, cn in self._norm.items()),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.86 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
            return scored[0][1]
        return None
