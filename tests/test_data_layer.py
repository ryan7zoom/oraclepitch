"""
tests/test_data_layer.py

Offline tests for engine/data/ - sample payloads shaped like the real
football-data.co.uk CSV and ESPN JSON (verified field names from the live
GitHub Actions check).
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from engine.data import football_data, update
from engine.data.espn import (BudgetExhausted, EspnEvent, half_time_from_details,
                              parse_event, parse_summary_stats)
from engine.data.model import Match, season_of
from engine.data.store import Store
from engine.data.teams import TeamResolver

FD_CSV = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HS,AS,HST,AST,HC,AC,HY,AY,HR,AR
E0,08/11/2026,16:30,Arsenal,Man United,2,1,H,1,0,15,9,6,3,7,4,1,2,0,0
E0,08/11/26,14:00,Chelsea,Everton,0,0,D,0,0,10,8,3,2,5,6,2,1,0,1
E0,,,,,,,,,,,,,,,,,,,
E0,09/11/2026,14:00,Fulham,Leeds,,,,,,,,,,,,,,,
"""


class TestFootballData(unittest.TestCase):
    def test_parse_rows_dates_and_unfinished(self):
        ms = football_data.parse_csv(FD_CSV, "epl", 2026)
        self.assertEqual(len(ms), 2)               # blank row and unplayed row dropped
        a = ms[0]
        self.assertEqual((a.home, a.away, a.date), ("Arsenal", "Man United", date(2026, 11, 8)))
        self.assertEqual((a.ft_h, a.ft_a, a.ht_h, a.ht_a), (2, 1, 1, 0))
        self.assertEqual((a.sh_h, a.sot_a, a.co_h, a.yc_a, a.rc_a), (15, 3, 7, 2, 0))
        self.assertIsNone(a.of_h)                  # offsides not in football-data
        self.assertEqual(ms[1].date, date(2026, 11, 8))   # 2-digit year parsed
        self.assertEqual(ms[1].rc_a, 1)

    def test_season_code_and_season_of(self):
        self.assertEqual(football_data.season_code(2026), "2627")
        self.assertEqual(season_of(date(2026, 11, 22)), 2026)
        self.assertEqual(season_of(date(2027, 3, 1)), 2026)


class TestTeamResolver(unittest.TestCase):
    def test_resolution(self):
        epl = TeamResolver("epl", {"Man United", "Man City", "Newcastle", "Nott'm Forest", "Wolves", "Brighton"})
        self.assertEqual(epl.resolve("Manchester United"), "Man United")
        self.assertEqual(epl.resolve("Manchester City"), "Man City")
        self.assertEqual(epl.resolve("Newcastle United"), "Newcastle")
        self.assertEqual(epl.resolve("Nottingham Forest"), "Nott'm Forest")
        self.assertEqual(epl.resolve("Wolverhampton Wanderers"), "Wolves")
        self.assertEqual(epl.resolve("Brighton & Hove Albion"), "Brighton")
        self.assertIsNone(epl.resolve("Totally Unknown FC"))
        self.assertIn("Totally Unknown FC", epl.unmatched)

        ll = TeamResolver("la_liga", {"Celta", "Real Madrid", "Ath Madrid", "Ath Bilbao", "Sociedad", "Barcelona"})
        self.assertEqual(ll.resolve("Celta Vigo"), "Celta")
        self.assertEqual(ll.resolve("Real Madrid"), "Real Madrid")
        self.assertEqual(ll.resolve("Atlético Madrid"), "Ath Madrid")
        self.assertEqual(ll.resolve("Athletic Club"), "Ath Bilbao")
        self.assertEqual(ll.resolve("Real Sociedad"), "Sociedad")
        self.assertEqual(ll.resolve("FC Barcelona"), "Barcelona")

        sa = TeamResolver("serie_a", {"Inter", "Milan", "Napoli", "Verona"})
        self.assertEqual(sa.resolve("Inter Milan"), "Inter")     # must not become Milan
        self.assertEqual(sa.resolve("AC Milan"), "Milan")
        self.assertEqual(sa.resolve("Hellas Verona"), "Verona")

        bl = TeamResolver("bundesliga", {"Bayern Munich", "M'gladbach", "Ein Frankfurt", "Stuttgart", "FC Koln"})
        self.assertEqual(bl.resolve("Borussia Mönchengladbach"), "M'gladbach")
        self.assertEqual(bl.resolve("Eintracht Frankfurt"), "Ein Frankfurt")
        self.assertEqual(bl.resolve("VfB Stuttgart"), "Stuttgart")
        self.assertEqual(bl.resolve("1. FC Köln"), "FC Koln")

        l1 = TeamResolver("ligue_1", {"Paris SG", "Lyon", "Strasbourg", "Brest"})
        self.assertEqual(l1.resolve("Paris Saint-Germain"), "Paris SG")
        self.assertEqual(l1.resolve("Olympique Lyonnais"), "Lyon")
        self.assertEqual(l1.resolve("Brest"), "Brest")


def _event(goals=None, home_score=2, away_score=1, completed=True):
    return {
        "id": "700001", "date": "2026-11-21T17:30Z",
        "status": {"type": {"completed": completed}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": str(home_score), "team": {"id": "10", "displayName": "Arsenal"}},
                {"homeAway": "away", "score": str(away_score), "team": {"id": "20", "displayName": "Newcastle United"}},
            ],
            "details": goals if goals is not None else [],
        }],
    }


def _goal(team_id, shown, own=False):
    return {"scoringPlay": True, "ownGoal": own, "team": {"id": team_id}, "clock": {"displayValue": shown}}


class TestEspnParsing(unittest.TestCase):
    def test_event_and_half_time(self):
        goals = [_goal("10", "12'"), _goal("20", "45'+2'"), _goal("10", "88'")]
        ev = parse_event(_event(goals))
        self.assertEqual((ev.home_name, ev.away_name, ev.date), ("Arsenal", "Newcastle United", date(2026, 11, 21)))
        self.assertEqual(ev.ht, (1, 1))            # 45'+2' is still the first half

    def test_half_time_rejected_when_goals_do_not_add_up(self):
        ev = parse_event(_event([_goal("10", "12'")], home_score=2, away_score=1))
        self.assertIsNone(ev.ht)

    def test_own_goal_either_way(self):
        # 1-0 final, own goal at 30'. ESPN may list the scoring or the conceding team.
        for team in ("10", "20"):
            ht = half_time_from_details([_goal(team, "30'", own=True)], "10", "20", 1, 0) \
                if team == "20" else half_time_from_details([_goal(team, "30'", own=True)], "10", "20", 0, 1)
            self.assertIn(ht, [(1, 0), (0, 1)])

    def test_not_completed(self):
        self.assertFalse(parse_event(_event(completed=False)).completed)

    def test_summary_stats(self):
        data = {"boxscore": {"teams": [
            {"homeAway": "home", "statistics": [
                {"name": "wonCorners", "displayValue": "6"}, {"name": "yellowCards", "displayValue": "3"},
                {"name": "redCards", "displayValue": "0"}, {"name": "totalShots", "displayValue": "12"},
                {"name": "shotsOnTarget", "displayValue": "3"}, {"name": "offsides", "displayValue": "3"},
                {"name": "possessionPct", "displayValue": "52"}]},
            {"homeAway": "away", "statistics": [
                {"name": "wonCorners", "displayValue": "3"}, {"name": "offsides", "displayValue": "1"}]},
        ]}}
        s = parse_summary_stats(data)
        self.assertEqual((s["co_h"], s["yc_h"], s["rc_h"], s["sh_h"], s["sot_h"], s["of_h"]), (6, 3, 0, 12, 3, 3))
        self.assertEqual((s["co_a"], s["of_a"]), (3, 1))
        self.assertNotIn("possession", "".join(s))
        self.assertIsNone(parse_summary_stats({}))


class TestStore(unittest.TestCase):
    def test_espn_only_then_football_data_replaces_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Store("epl", os.path.join(tmp, "epl.csv"))
            espn_row = Match("epl", 2026, date(2026, 11, 21), "Arsenal", "Newcastle",
                             ft_h=2, ft_a=1, sh_h=12, sh_a=13, of_h=3, of_a=3, espn_id="7")
            st.add_from_espn(espn_row)
            fd = Match("epl", 2026, date(2026, 11, 22), "Arsenal", "Newcastle",   # +1 day tolerance
                       ft_h=2, ft_a=1, ht_h=1, ht_a=0, sh_h=14, sh_a=11)
            st.upsert_fd(fd)
            self.assertEqual(len(st.rows), 1)
            m = st.rows[0]
            self.assertEqual((m.sh_h, m.sh_a, m.ht_h), (14, 11, 1))     # football-data wins
            self.assertEqual((m.of_h, m.of_a, m.espn_id), (3, 3, "7"))  # offsides kept
            self.assertEqual(m.sources, "espn+fd")

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.csv")
            st = Store("epl", p)
            st.upsert_fd(Match("epl", 2026, date(2026, 11, 8), "A", "B", ft_h=1, ft_a=0, of_h=2))
            st.save()
            st2 = Store("epl", p)
            st2.load()
            self.assertEqual(st2.rows[0].of_h, 2)
            self.assertIsNone(st2.rows[0].of_a)


class FakeEspn:
    """Stands in for EspnClient: a scoreboard per date and a summary per event."""
    def __init__(self, boards, budget=1000):
        self.boards, self.budget, self.used = boards, budget, 0

    def _use(self):
        if self.used >= self.budget:
            raise BudgetExhausted()
        self.used += 1

    def scoreboard(self, league, day):
        self._use()
        return self.boards.get(day, [])

    def summary(self, league, event_id):
        self._use()
        return {"of_h": 2, "of_a": 1, "sh_h": 10, "sh_a": 9}


class TestUpdate(unittest.TestCase):
    def _run(self, espn, tmp, rows):
        today = date(2026, 11, 22)
        with patch.object(config, "MATCH_DATA_DIR", tmp), \
             patch.object(config, "ESPN_FILL_OFFSIDES", True), \
             patch.object(football_data, "fetch_season", return_value=rows):
            return update.run(today, ["epl"], budget=1000, full=False, use_espn=True, espn=espn)

    def test_offsides_backfilled_new_match_added_and_unfindable_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            fd_rows = [
                Match("epl", 2026, date(2026, 11, 8), "Arsenal", "Man United", ft_h=2, ft_a=1),
                Match("epl", 2026, date(2026, 11, 7), "Chelsea", "Everton", ft_h=0, ft_a=0),
            ]
            boards = {
                date(2026, 11, 8): [EspnEvent("1", date(2026, 11, 8), True, "Arsenal", "Manchester United", 2, 1, None)],
                date(2026, 11, 21): [EspnEvent("2", date(2026, 11, 21), True, "Arsenal", "Newcastle United", 2, 1, (1, 1)),
                                     EspnEvent("3", date(2026, 11, 21), True, "Mystery FC", "Chelsea", 1, 1, None)],
            }
            # Newcastle must exist as a canonical name: add an older Newcastle match
            fd_rows.append(Match("epl", 2026, date(2026, 10, 1), "Newcastle", "Chelsea", ft_h=1, ft_a=1, of_h=1, of_a=1))
            rep = self._run(FakeEspn(boards), tmp, fd_rows)["epl"]
            st = Store("epl", os.path.join(tmp, "epl.csv"))
            st.load()
            by = {(m.home, m.away): m for m in st.rows}
            self.assertEqual(by[("Arsenal", "Man United")].of_h, 2)           # backfilled
            new = by[("Arsenal", "Newcastle")]                                 # ESPN-only match added
            self.assertEqual((new.ft_h, new.ht_h, new.of_a, new.sources), (2, 1, 1, "espn"))
            self.assertEqual(by[("Chelsea", "Everton")].espn_tried, 1)         # ESPN had nothing for it
            self.assertIn("Mystery FC", rep["unmatched_espn_names"])
            self.assertEqual(rep["offsides_missing_last_2_seasons"], 1)

    def test_budget_exhaustion_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            fd_rows = [Match("epl", 2026, date(2026, 11, 8), "Arsenal", "Man United", ft_h=2, ft_a=1)]
            rep = self._run(FakeEspn({}, budget=2), tmp, fd_rows)["epl"]
            self.assertTrue(rep.get("budget_exhausted"))
            self.assertTrue(os.path.exists(os.path.join(tmp, "epl.csv")))     # progress still saved


if __name__ == "__main__":
    unittest.main()
