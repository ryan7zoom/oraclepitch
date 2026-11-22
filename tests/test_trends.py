"""
tests/test_trends.py

Offline tests for engine/trends/. Uses invented matches only.
Run:  python -m unittest tests.test_trends   (or pytest tests/test_trends.py)
"""

import os
import random
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.data.model import Match
from engine.trends import rules
from datetime import datetime, timezone
from engine.trends.engine import Fixture, TrendEngine, build_report
from engine.trends.select import Bet, conflicts, select_bets
from engine.trends import settings as S
from engine.trends.markets import SeqData, build_catalog, market_flags

# Every "N of last M" line on the Scores24 Barcelona - Villarreal page.
REFERENCE_LINES = [
    (15, 16), (14, 16), (11, 12), (14, 15), (11, 12), (8, 9), (15, 16), (11, 12),
    (27, 28), (25, 26), (20, 21), (18, 19), (32, 33), (22, 23), (18, 18), (16, 16),
    (24, 26), (15, 16), (13, 13), (7, 8), (26, 27), (15, 17), (15, 16), (13, 14),
    (12, 13), (7, 8), (14, 16), (11, 12), (18, 18), (14, 14), (9, 9), (7, 7),
    (12, 12), (8, 8), (6, 7), (5, 6), (7, 8), (6, 7), (17, 19), (9, 10), (9, 10),
    (7, 8), (6, 7), (5, 5), (18, 18), (10, 11), (8, 8), (9, 10), (6, 7), (9, 9),
    (6, 6), (7, 7), (5, 5), (5, 5), (6, 6), (5, 5), (9, 10), (8, 9), (8, 9), (5, 5),
    (17, 18), (11, 12), (16, 18), (12, 13), (8, 9), (13, 15), (11, 12), (13, 15), (11, 12),
]


def mk(d, home, away, ft=(1, 1), ht=(0, 0), **kw):
    m = Match(league="la_liga", season=d.year if d.month >= 7 else d.year - 1,
              date=d, home=home, away=away, ft_h=ft[0], ft_a=ft[1],
              ht_h=ht[0] if ht else None, ht_a=ht[1] if ht else None)
    for k, v in kw.items():
        setattr(m, k, v)
    return m


FX_DATE = date(2026, 11, 22)


def fx(home="A", away="B"):
    return Fixture("la_liga", FX_DATE, home, away)


def find(trends, **cond):
    return [t for t in trends if all(getattr(t, k) == v for k, v in cond.items())]


class TestRule(unittest.TestCase):
    def test_every_reference_page_line_passes(self):
        for hits, size in REFERENCE_LINES:
            self.assertTrue(rules.passes(size, hits), f"{hits} of {size} should pass")

    def test_rejects_what_the_page_never_shows(self):
        self.assertFalse(rules.passes(4, 4))      # below minimum window
        self.assertFalse(rules.passes(5, 4))      # a miss in a 5-game window
        self.assertFalse(rules.passes(10, 8))     # 2 misses before game 15
        self.assertFalse(rules.passes(20, 17))    # 3 misses

    def test_longest_window_and_oldest_must_be_hit(self):
        self.assertEqual(rules.find_window([True] * 5 + [False]).size, 5)
        w = rules.find_window([True] * 10 + [False] + [True] * 8)
        self.assertEqual((w.size, w.hits), (19, 18))
        # the oldest game is a miss -> window is cut back to end on a hit
        w = rules.find_window([True] * 8 + [False, False])
        self.assertEqual((w.size, w.hits), (8, 8))
        self.assertIsNone(rules.find_window([True] * 4))
        self.assertIsNone(rules.find_window([False] * 10))

    def test_unknown_is_skipped_not_counted(self):
        w = rules.find_window([True, None, True, True, None, True, True])
        self.assertEqual((w.size, w.hits, w.skipped), (5, 5, 2))


class TestMarkets(unittest.TestCase):
    def flags(self, key, matches, side="home"):
        cat = {m.key: m for m in build_catalog()}
        return market_flags(cat[key], SeqData([(m, side) for m in matches]))

    def test_second_half_goals_are_ft_minus_ht(self):
        m = mk(date(2026, 1, 1), "A", "B", ft=(3, 1), ht=(1, 1))      # 2H: 2-0
        self.assertEqual(self.flags("win:goals_2h", [m]), [True])
        self.assertEqual(self.flags("win:goals_1h", [m]), [False])    # 1H was 1-1
        self.assertEqual(self.flags("team:goals_2h:over:0.5", [m], "away"), [False])

    def test_unknown_halftime_gives_none(self):
        m = mk(date(2026, 1, 1), "A", "B", ft=(1, 0), ht=None)
        self.assertEqual(self.flags("win:goals_1h", [m]), [None])
        self.assertEqual(self.flags("win:goals", [m]), [True])

    def test_sides_and_totals(self):
        m = mk(date(2026, 1, 1), "A", "B", ft=(2, 0), co_h=7, co_a=3)
        self.assertEqual(self.flags("team:corners:over:6.5", [m], "home"), [True])
        self.assertEqual(self.flags("team:corners:over:6.5", [m], "away"), [False])
        self.assertEqual(self.flags("total:corners:under:10.5", [m]), [True])
        self.assertEqual(self.flags("total:corners:under:9.5", [m]), [False])
        self.assertEqual(self.flags("against:goals:over:0.5", [m], "away"), [True])   # B conceded 2
        self.assertEqual(self.flags("btts:yes", [m]), [False])
        self.assertEqual(self.flags("btts:no", [m]), [True])
        self.assertEqual(self.flags("not_lose", [m], "away"), [False])

    def test_none_stat_is_not_zero(self):
        m = mk(date(2026, 1, 1), "A", "B", of_h=None, of_a=2)
        self.assertEqual(self.flags("team:offsides:under:3.5", [m], "home"), [None])
        self.assertEqual(self.flags("total:offsides:under:3.5", [m]), [None])
        self.assertEqual(self.flags("team:offsides:under:3.5", [m], "away"), [True])


def season_history():
    """A home 8 games (won all, 2-0, 8 corners), away 8 games (lost all). B is poor. Other teams fill."""
    ms, d = [], date(2026, 8, 1)
    for i in range(8):
        d += timedelta(days=7)
        ms.append(mk(d, "A", "C", ft=(2, 0), ht=(1, 0), co_h=8, co_a=3, yc_h=1, yc_a=2))
        d += timedelta(days=3)
        ms.append(mk(d, "D", "A", ft=(2, 0), ht=(1, 0), co_h=6, co_a=2, yc_h=1, yc_a=3))
        d += timedelta(days=3)
        ms.append(mk(d, "B", "D", ft=(0, 1), ht=(0, 0), co_h=4, co_a=5, yc_h=2, yc_a=1))
    return ms


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TrendEngine({"la_liga": season_history()})
        self.trends = self.engine.trends_for_fixture(fx())

    def test_home_form_win_streak_found_with_exact_numbers(self):
        t = find(self.trends, market="win:goals", subject="A", scope="form_home")
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0].hits, t[0].window, t[0].about), (8, 8, "home"))
        self.assertEqual(t[0].text, "A won in last 8 home games (La Liga)")

    def test_all_games_win_streak_not_found_because_away_games_were_lost(self):
        self.assertEqual(find(self.trends, market="win:goals", subject="A", scope="form_all"), [])

    def test_away_team_scopes(self):
        t = find(self.trends, subject="B", scope="form_away", market="not_lose")
        self.assertEqual(t, [])                                   # B lost nothing? it lost 0-1 ... as home only
        self.assertTrue(all(t.scope != "form_home" for t in find(self.trends, subject="B")))

    def test_text_style(self):
        t = find(self.trends, market="team:corners:over:7.5", subject="A", scope="form_home")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].text, "A took over 7.5 corners in last 8 home games (La Liga)")
        self.assertEqual(t[0].bet, "A corners Over 7.5")

    def test_total_text_has_team_name(self):
        # A's home games had 11 corners each -> tightest line that holds is 10.5
        t = find(self.trends, market="total:corners:over:10.5", subject="A", scope="form_home")
        self.assertEqual(t[0].text, "Over 10.5 corners in last 8 A home games (La Liga)")
        self.assertEqual((t[0].about, t[0].bet), ("total", "Total corners Over 10.5"))

    def test_conceded_maps_to_the_other_team(self):
        # B's away games: not applicable; use D-free check on A: A conceded 0 at home
        t = find(self.trends, subject="A", scope="form_home", market="against:goals:under:0.5")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].about, "away")                      # the bet is about B's goals
        self.assertEqual(t[0].text, "A kept a clean sheet in last 8 home games (La Liga)")

    def test_no_future_leakage(self):
        late = Fixture("la_liga", date(2026, 8, 20), "A", "B")    # only the first 2 weeks exist
        for t in self.engine.trends_for_fixture(late):
            self.assertLessEqual(t.window, 3)                     # < MIN_WINDOW games existed
        self.assertEqual(self.engine.trends_for_fixture(late), [])

    def test_base_rate_attached_and_trivial_lines_dropped(self):
        from unittest.mock import patch
        with patch.object(S, "MIN_BASE_SAMPLE", 10):          # the tiny test league has only 24 matches
            tr = TrendEngine({"la_liga": season_history()}).trends_for_fixture(fx())
        self.assertTrue(all(t.base_rate is not None and t.base_rate < S.MAX_BASE_RATE for t in tr))
        # nobody ever scores 5+ -> 'team goals under 4.5' is 100% in the league -> hidden
        self.assertEqual(find(tr, market="team:goals:under:4.5"), [])
        # a rare-in-league event that A does every time is kept, with its rate attached
        t = find(tr, market="team:corners:over:7.5", subject="A", scope="form_home")[0]
        self.assertLess(t.base_rate, 0.5)

    def test_h2h_scopes_and_orientation(self):
        ms = season_history()
        d = date(2025, 9, 1)
        for i in range(7):                                        # 7 meetings, A always wins
            d += timedelta(days=30)
            home, away = ("A", "B") if i % 2 == 0 else ("B", "A")
            ft = (2, 0) if home == "A" else (0, 2)
            ms.append(mk(d, home, away, ft=ft, ht=(0, 0) if home == "A" else (0, 1)))
        eng = TrendEngine({"la_liga": ms})
        tr = eng.trends_for_fixture(fx())
        w = find(tr, market="win:goals", subject="A", scope="h2h_all")
        self.assertEqual((w[0].hits, w[0].window), (7, 7))
        self.assertEqual(w[0].text, "A won in last 7 games against B (La Liga)")
        self.assertEqual(find(tr, market="win:goals", subject="B", scope="h2h_all"), [])
        # only 4 meetings at A's ground (< 5) -> no venue trend
        self.assertEqual(find(tr, scope="h2h_venue"), [])
        # shared (total) markets appear once, under the home team
        shared = [t for t in tr if t.scope == "h2h_all" and t.kind == "total"]
        self.assertTrue(all(t.subject == "A" for t in shared))

    def test_build_report_skips_unknown_team(self):
        rep = build_report(self.engine, [fx(), fx("A", "ZZZ")])
        self.assertEqual(len(rep["fixtures"]), 1)
        self.assertEqual(len(rep["skipped"]), 1)
        self.assertIn("ZZZ", rep["skipped"][0]["reason"])
        self.assertEqual(len(rep["fixtures"][0]["bets"]), len(select_bets(self.trends)))

    def test_unknown_offsides_skipped_inside_window(self):
        ms = season_history()
        for m in ms:
            m.of_h = m.of_a = 1 if "A" in (m.home, m.away) else 5
        ms[9].of_h = None                                          # a mid-history game, A at home
        eng = TrendEngine({"la_liga": ms})
        t = find(eng.trends_for_fixture(fx()), market="team:offsides:under:1.5",
                 subject="A", scope="form_all")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].window, 15)                          # 16 A games, 1 unknown skipped
        self.assertEqual(t[0].skipped_unknown, 1)


class TestWindowCapAndLuck(unittest.TestCase):
    def test_window_never_exceeds_cap(self):
        w = rules.find_window([True] * 100)
        self.assertEqual(w.size, S.MAX_WINDOW)

    def test_tail_probability(self):
        self.assertAlmostEqual(rules.tail_p(8, 8, 0.5), 0.5 ** 8)
        self.assertLess(rules.tail_p(15, 15, 0.45), rules.tail_p(8, 8, 0.45))
        self.assertGreater(rules.tail_p(10, 10, 0.97), 0.7)       # trivial event: not remarkable


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.engine = TrendEngine({"la_liga": season_history()})
        self.raw = self.engine.trends_for_fixture(fx())
        self.bets = select_bets(self.raw)

    def test_far_fewer_bets_than_raw_trends(self):
        self.assertGreater(len(self.raw), 3 * len(self.bets))
        self.assertGreater(len(self.bets), 0)

    def test_one_line_per_team_scope_stat_direction(self):
        # A's home corners: over 7.5 (tightest line holding 8 of 8) must be the one shown, not 6.5 / 5.5
        labels = [b.label for b in self.bets]
        self.assertIn("A corners Over 7.5", labels)
        self.assertNotIn("A corners Over 6.5", labels)

    def test_bet_groups_all_trends_at_its_line(self):
        b = [b for b in self.bets if b.label == "A to win"][0]
        scopes = {t.scope for t in b.trends}
        self.assertIn("form_home", scopes)
        self.assertTrue(all(t.p <= S.MAX_P for t in b.trends))

    def test_caps_respected(self):
        for g, cap in S.BET_CAPS.items():
            self.assertLessEqual(len([b for b in self.bets if b.group == g]), cap)
        self.assertLessEqual(max(len(b.trends) for b in self.bets), S.MAX_TRENDS_PER_BET)

    def test_no_contradictions(self):
        for i, a in enumerate(self.bets):
            for b in self.bets[i + 1:]:
                self.assertFalse(conflicts(a, b), f"{a.label} vs {b.label}")

    def test_conflict_rules(self):
        def bet(kind, stat, direction, line, about):
            return Bet("k", "g", "l", about, kind, stat, direction, line, 0.001, None)
        self.assertTrue(conflicts(bet("total", "corners", "over", 9.5, "total"),
                                  bet("total", "corners", "under", 9.5, "total")))
        self.assertTrue(conflicts(bet("total", "corners", "over", 9.5, "total"),
                                  bet("total", "corners", "under", 10.5, "total")))
        self.assertFalse(conflicts(bet("total", "corners", "over", 9.5, "total"),
                                   bet("total", "corners", "under", 12.5, "total")))   # a range is fine
        self.assertTrue(conflicts(bet("result", "goals", "win", None, "home"),
                                  bet("result", "goals", "win", None, "away")))
        self.assertTrue(conflicts(bet("result", "goals", "win", None, "home"),
                                  bet("result", "goals", "not_lose", None, "away")))
        self.assertFalse(conflicts(bet("result", "goals", "not_lose", None, "home"),
                                   bet("result", "goals", "not_lose", None, "away")))
        self.assertTrue(conflicts(bet("btts", "goals", "yes", None, "total"),
                                  bet("team", "goals", "under", 0.5, "away")))

    def test_conceded_goals_merge_into_the_team_goals_bet(self):
        # A kept clean sheets at home -> supports "B not to score"; same bet as B's own 'not score' trends
        keys = {t.bet_key for t in self.raw if t.market == "against:goals:under:0.5"}
        self.assertTrue(all(k.startswith("team:goals:under:0.5|") for k in keys))

    def test_report_contains_bets_not_raw(self):
        rep = build_report(self.engine, [fx()])
        f = rep["fixtures"][0]
        self.assertEqual(len(f["bets"]), len(self.bets))
        self.assertGreater(f["raw_trend_count"], len(f["bets"]))
        self.assertEqual(set(f["bets"][0]), {"group", "label", "about", "stat", "direction", "line",
                                             "p", "base_rate", "trends"})


def _espn_event(i, home, away, when, state="pre", completed=False):
    return {"id": i, "date": when, "status": {"type": {"completed": completed, "state": state}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": home}, "score": "0"},
                {"homeAway": "away", "team": {"id": "2", "displayName": away}, "score": "0"}],
                "details": []}]}


class FakeScoreboardEspn:
    def __init__(self, events_by_day):
        self.events_by_day, self.used = events_by_day, 0

    def scoreboard(self, league, day):
        from engine.data.espn import parse_scoreboard
        self.used += 1
        return parse_scoreboard({"events": self.events_by_day.get((league, day), [])})


class TestFixturesAndPage(unittest.TestCase):
    NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)

    def _fixtures(self):
        from engine.trends.fixtures import upcoming_fixtures
        board = {("la_liga", date(2026, 11, 22)): [
            _espn_event("1", "A", "B", "2026-11-22T20:00Z"),
            _espn_event("2", "A", "Nobody FC", "2026-11-22T20:00Z"),
            _espn_event("3", "C", "D", "2026-11-22T03:00Z", state="in"),          # already started
            _espn_event("4", "C", "D", "2026-11-22T01:00Z", state="post", completed=True)]}
        names = {"la_liga": {"A", "B", "C", "D"}}
        return upcoming_fixtures(FakeScoreboardEspn(board), ["la_liga"], names,
                                 date(2026, 11, 22), 1, self.NOW)

    def test_only_not_started_matches_and_unmatched_reported(self):
        fixtures, skipped = self._fixtures()
        self.assertEqual([(f.home, f.away) for f in fixtures], [("A", "B")])
        self.assertEqual(fixtures[0].kickoff, datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc))
        self.assertEqual([s["away"] for s in skipped], ["Nobody FC"])

    def test_event_dedupe_across_days(self):
        from engine.trends.fixtures import upcoming_fixtures
        ev = _espn_event("1", "A", "B", "2026-11-23T01:00Z")
        board = {("la_liga", date(2026, 11, 22)): [ev], ("la_liga", date(2026, 11, 23)): [ev]}
        fixtures, _ = upcoming_fixtures(FakeScoreboardEspn(board), ["la_liga"], {"la_liga": {"A", "B"}},
                                        date(2026, 11, 22), 1, self.NOW)
        self.assertEqual(len(fixtures), 1)

    def test_page_and_run_end_to_end(self):
        import json, tempfile
        from engine.trends.page import render_page
        from engine.trends.run import run
        from engine.data.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            st = Store("la_liga", os.path.join(tmp, "la_liga.csv"))
            for m in season_history():
                st._add(m)
            st.save()
            board = {("la_liga", date(2026, 11, 22)): [_espn_event("1", "A", "B", "2026-11-22T13:00Z")]}
            out = run(date(2026, 11, 22), 0, FakeScoreboardEspn(board), tmp,
                      os.path.join(tmp, "t.json"), os.path.join(tmp, "index.html"), now=self.NOW)
            self.assertEqual(len(out["fixtures"]), 1)
            html = open(os.path.join(tmp, "index.html"), encoding="utf-8").read()
            self.assertIn("A v B", html)
            self.assertIn("21:00", html)                      # 13:00 UTC shown in GMT+8
            self.assertIn("A to win", html)
            saved = json.load(open(os.path.join(tmp, "t.json")))
            self.assertEqual(saved["fixtures"][0]["home"], "A")

    def test_page_escapes_and_handles_no_fixtures(self):
        from engine.trends.page import render_page
        html = render_page({"fixtures": [], "skipped": []}, self.NOW)
        self.assertIn("No fixtures", html)
        f = {"league": "epl", "date": "2026-11-22", "kickoff": None, "home": "<b>X</b>", "away": "Y",
             "raw_trend_count": 0, "bets": []}
        self.assertNotIn("<b>X</b>", render_page({"fixtures": [f], "skipped": []}, self.NOW))


class TestSmoke(unittest.TestCase):
    def test_random_league_runs_and_is_deterministic(self):
        rnd = random.Random(1)
        teams = [f"T{i}" for i in range(20)]
        ms, d = [], date(2022, 8, 1)
        for _ in range(4 * 380):
            h, a = rnd.sample(teams, 2)
            d += timedelta(days=1) if rnd.random() < 0.5 else timedelta(days=0)
            ms.append(mk(d, h, a, ft=(rnd.randint(0, 4), rnd.randint(0, 3)),
                         ht=(rnd.randint(0, 2), rnd.randint(0, 2)),
                         sh_h=rnd.randint(5, 25), sh_a=rnd.randint(4, 20),
                         sot_h=rnd.randint(1, 9), sot_a=rnd.randint(1, 8),
                         co_h=rnd.randint(2, 10), co_a=rnd.randint(1, 9),
                         yc_h=rnd.randint(0, 4), yc_a=rnd.randint(0, 4),
                         of_h=rnd.randint(0, 5), of_a=rnd.randint(0, 5)))
        for m in ms:                                              # make HT <= FT
            m.ht_h, m.ht_a = min(m.ht_h, m.ft_h), min(m.ht_a, m.ft_a)
        eng = TrendEngine({"la_liga": ms})
        f = Fixture("la_liga", d + timedelta(days=1), "T3", "T7")
        a, b = eng.trends_for_fixture(f), eng.trends_for_fixture(f)
        self.assertEqual([t.text for t in a], [t.text for t in b])
        for t in a:
            self.assertTrue(rules.passes(t.window, t.hits))


if __name__ == "__main__":
    unittest.main()
