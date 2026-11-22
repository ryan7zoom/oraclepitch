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
from engine.trends.engine import Fixture, Trend, TrendEngine, build_report
from engine.trends.fair import FairModel, Expect, p_over, pmf
from engine.trends.select import Bet, conflicts, select_bets
from engine.trends import settings as S
from engine.trends.markets import SeqData, build_catalog, market_flags

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
    def test_longest_good_window_wins(self):
        w = rules.pick_window([True] * 9 + [False] * 3, S.FORM_WINDOWS)       # 12 games, 9 hits
        self.assertEqual((w.size, w.hits), (12, 9))

    def test_five_of_eight_is_no_longer_shown(self):
        self.assertIsNone(rules.pick_window([True] * 5 + [False] * 3, S.FORM_WINDOWS))     # 62.5%: below the 70% floor
        w = rules.pick_window([True] * 7 + [False] * 3, S.FORM_WINDOWS)                    # 7/10 = 70%: fine
        self.assertEqual((w.size, w.hits), (10, 7))

    def test_a_good_window_beats_a_longer_weaker_one(self):
        flags = [True] * 8 + [False] * 2 + [False] * 10       # 8/8, 8/10 (80%), 8/12 (67%), 8/15 ...
        w = rules.pick_window(flags, S.FORM_WINDOWS)
        self.assertEqual((w.size, w.hits), (10, 8))

    def test_rejects_weak_and_small_samples(self):
        self.assertIsNone(rules.pick_window([True] * 4 + [False] * 4, S.FORM_WINDOWS))     # 50%
        self.assertIsNone(rules.pick_window([True] * 7, S.FORM_WINDOWS))                   # under 8 games

    def test_unknown_is_skipped_not_counted(self):
        w = rules.pick_window([True, None, True, True, None] + [True] * 6, S.FORM_WINDOWS)
        self.assertEqual((w.size, w.hits), (8, 8))

    def test_window_sizes_are_fixed(self):
        for hits in range(0, 31):
            w = rules.pick_window([True] * hits + [False] * (30 - hits), S.FORM_WINDOWS)
            if w:
                self.assertIn(w.size, S.FORM_WINDOWS)


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
    """A home 12 games (won all, 2-0, 8 corners), away 12 games (lost all). B is poor. Other teams fill."""
    ms, d = [], date(2026, 6, 1)
    for i in range(12):
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
        self.assertEqual((t[0].hits, t[0].window, t[0].about), (12, 12, "home"))
        self.assertEqual(t[0].text, "A won in last 12 home games (La Liga)")

    def test_all_games_win_not_found_because_away_games_were_lost(self):
        self.assertEqual(find(self.trends, market="win:goals", subject="A", scope="form_all"), [])

    def test_away_team_scopes(self):
        self.assertTrue(all(t.scope != "form_home" for t in find(self.trends, subject="B")))

    def test_text_style(self):
        t = find(self.trends, market="team:corners:over:7.5", subject="A", scope="form_home")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].text, "A took over 7.5 corners in last 12 home games (La Liga)")
        self.assertEqual(t[0].bet, "A corners Over 7.5")

    def test_total_text_has_team_name(self):
        t = find(self.trends, market="total:corners:over:10.5", subject="A", scope="form_home")
        self.assertEqual(t[0].text, "Over 10.5 corners in last 12 A home games (La Liga)")
        self.assertEqual((t[0].about, t[0].bet), ("total", "Total corners Over 10.5"))

    def test_conceded_maps_to_the_other_team(self):
        t = find(self.trends, subject="A", scope="form_home", market="against:goals:under:0.5")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].about, "away")                      # the bet is about B's goals
        self.assertEqual(t[0].text, "A kept a clean sheet in last 12 home games (La Liga)")

    def test_no_future_leakage(self):
        early = Fixture("la_liga", date(2026, 6, 20), "A", "B")   # only the first 2 rounds exist
        self.assertEqual(self.engine.trends_for_fixture(early), [])

    def test_mixed_hit_rates_are_shown_as_counts(self):
        ms = season_history()
        for i, m in enumerate(m for m in ms if m.home == "A"):    # A's home corners: 8 in 9 games, 3 in 3
            m.co_h = 8 if i % 4 else 3
        tr = TrendEngine({"la_liga": ms}).trends_for_fixture(fx())
        t = find(tr, market="team:corners:over:7.5", subject="A", scope="form_home")[0]
        self.assertEqual((t.hits, t.window), (9, 12))
        self.assertEqual(t.text, "A took over 7.5 corners in 9 of last 12 home games (La Liga)")

    def test_h2h_scopes_and_orientation(self):
        ms = season_history()
        d = date(2025, 9, 1)
        for i in range(7):                                        # 7 meetings, A always wins
            d += timedelta(days=30)
            home, away = ("A", "B") if i % 2 == 0 else ("B", "A")
            ft = (2, 0) if home == "A" else (0, 2)
            ms.append(mk(d, home, away, ft=ft, ht=(0, 0) if home == "A" else (0, 1)))
        tr = TrendEngine({"la_liga": ms}).trends_for_fixture(fx())
        w = find(tr, market="win:goals", subject="A", scope="h2h_all")
        self.assertEqual((w[0].hits, w[0].window), (6, 6))        # fixed windows: 5, 6, 8, 10
        self.assertEqual(w[0].text, "A won in last 6 games against B (La Liga)")
        self.assertEqual(find(tr, market="win:goals", subject="B", scope="h2h_all"), [])
        self.assertEqual(find(tr, scope="h2h_venue"), [])         # only 4 meetings at A's ground (< 5)
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
        t = find(TrendEngine({"la_liga": ms}).trends_for_fixture(fx()), market="team:offsides:under:1.5",
                 subject="A", scope="form_all")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].window, 20)                          # 24 A games, 1 unknown skipped, 23 known

    def test_no_model_on_a_tiny_league_means_nothing_is_filtered(self):
        self.assertTrue(all(t.model_p is None for t in self.trends))


def league_history(att, dfn, rounds=2, base=12.0):
    """Noise-free league: shots = base * attack(team) * defence(opponent). T0..T11, home and away."""
    teams = [f"T{i}" for i in range(12)]
    ms, d = [], FX_DATE - timedelta(days=330)
    for _ in range(rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                d += timedelta(days=1)
                ms.append(mk(d, h, a, ft=(1, 1), ht=(0, 0),
                             sh_h=round(base * att.get(h, 1.0) * dfn.get(a, 1.0)),
                             sh_a=round(base * att.get(a, 1.0) * dfn.get(h, 1.0))))
    return ms


class TestFair(unittest.TestCase):
    def setUp(self):
        att = {"T0": 1.4, "T1": 0.7}                    # T0 attacks a lot, T1 hardly at all
        dfn = {"T2": 1.3, "T3": 0.7}                    # T2 allows a lot, T3 allows little
        self.model = FairModel(league_history(att, dfn), FX_DATE)

    def test_opponent_changes_the_fair_line(self):
        loose = self.model.expect("T0", "T2").mu("shots", "home")
        tight = self.model.expect("T0", "T3").mu("shots", "home")
        self.assertGreater(loose, tight + 5)             # same attack, very different opponent
        self.assertAlmostEqual(loose, 12 * 1.4 * 1.3, delta=2.0)
        self.assertAlmostEqual(tight, 12 * 1.4 * 0.7, delta=2.0)

    def test_weak_attack_is_lower_than_strong_attack_against_the_same_opponent(self):
        self.assertGreater(self.model.expect("T0", "T5").mu("shots", "home"),
                           self.model.expect("T1", "T5").mu("shots", "home") + 5)

    def test_stats_without_data_have_no_estimate(self):
        self.assertIsNone(self.model.expect("T0", "T2").mu("corners", "home"))

    def test_future_games_are_not_used(self):
        ms = league_history({"T0": 1.4}, {"T2": 1.3})
        before = FairModel(ms, FX_DATE).expect("T0", "T2").mu("shots", "home")
        ms.append(mk(FX_DATE, "T0", "T2", ft=(9, 0), ht=(5, 0), sh_h=90, sh_a=0))
        after = FairModel(ms, FX_DATE).expect("T0", "T2").mu("shots", "home")
        self.assertEqual(before, after)

    def test_too_little_history_gives_no_model(self):
        self.assertIsNone(FairModel(season_history(), FX_DATE).expect("A", "B"))

    def test_distributions(self):
        for mu, alpha in ((0.5, 0.0), (7.0, 0.05), (18.0, 0.04)):
            self.assertAlmostEqual(sum(pmf(mu, alpha, 120)), 1.0, places=6)
        self.assertGreater(p_over(18, 0.04, 11.5), p_over(18, 0.04, 17.5))
        self.assertGreater(p_over(18, 0.04, 17.5), p_over(18, 0.04, 22.5))
        self.assertAlmostEqual(p_over(2.0, 0.0, 0.5), 1 - 2.718281828 ** -2, places=4)

    def test_result_probabilities_add_up(self):
        ex = self.model.expect("T0", "T2")
        both = (ex.p_result("home", "not_lose", "goals") + ex.p_result("away", "win", "goals"))
        self.assertAlmostEqual(both, 1.0, places=6)
        self.assertAlmostEqual(ex.p_btts(True) + ex.p_btts(False), 1.0)

    def test_chalk_is_flagged_on_real_trends_and_never_shown(self):
        ms = league_history({"T0": 1.4}, {"T2": 1.3})
        eng = TrendEngine({"la_liga": ms})
        raw = eng.trends_for_fixture(Fixture("la_liga", FX_DATE, "T0", "T2"))
        easy = find(raw, market="team:shots:over:6.5", subject="T0", scope="form_home")
        self.assertTrue(easy and easy[0].model_p > 0.95)                  # T0 always has 10+ shots
        self.assertGreater(easy[0].fair, 15)
        shown = [b.label for b in select_bets(raw)]
        self.assertNotIn("T0 shots Over 6.5", shown)
        self.assertTrue(all(t.model_p is None or t.model_p >= 0 for t in raw))


def tr(line=15.5, hits=9, window=12, role="for", scope="form_home", model_p=0.5, direction="over",
       stat="shots", about="home", subject="A", kind="team", group="shots", fair=17.0, market=None):
    """A hand-made Trend for testing selection rules."""
    key = f"team:{stat}:{direction}:{line:g}|{about}"
    return Trend(league="la_liga", home="A", away="B", group=group, market=market or f"team:{stat}:{direction}:{line}",
                 bet=f"A {stat} {direction.title()} {line:g}", about=about, subject=subject, scope=scope, stat=stat,
                 kind="against" if role == "against" else kind, direction=direction, line=line, hits=hits,
                 window=window, misses=window - hits, skipped_unknown=0, text=f"{subject} {hits}/{window}",
                 model_p=model_p, fair=fair, bet_key=key, role=role, target_kind="team")


class TestSelection(unittest.TestCase):
    def labels(self, trends):
        return [b.label for b in select_bets(trends)]

    # ---- chalk -------------------------------------------------------------
    def test_chalk_is_dropped(self):
        both = [tr(15.5, 11, 12, role="for", model_p=0.92), tr(15.5, 11, 12, role="against", scope="form_away", model_p=0.92)]
        self.assertEqual(select_bets(both), [])

    def test_the_cutoff_is_inclusive_and_just_under_is_kept(self):
        for p, kept in ((S.CHALK_MAX_P_BY_MARKET[("shots", "over", "team")], True), (S.CHALK_MAX_P_BY_MARKET[("shots", "over", "team")] + 0.01, False)):
            self.assertEqual(bool(select_bets([tr(17.5, 9, 12, role="for", model_p=p),
                                               tr(17.5, 9, 12, role="against", scope="form_away", model_p=p)])), kept)

    def test_long_shots_the_matchup_contradicts_are_dropped(self):
        weird = [tr(9.5, 8, 10, direction="under", role="against", scope="form_all", model_p=0.04)]
        self.assertEqual(select_bets(weird), [])

    def test_no_model_means_no_filtering(self):
        self.assertEqual(len(select_bets([tr(17.5, 9, 12, model_p=None, role="for"),
                                          tr(17.5, 9, 12, model_p=None, role="against", scope="form_away")])), 1)

    # ---- sections ------------------------------------------------------------
    def test_matching_needs_both_sides_in_form(self):
        both = select_bets([tr(17.5, 8, 12, role="for"), tr(17.5, 7, 10, role="against", scope="form_away")])
        self.assertTrue(both[0].matching)
        self.assertEqual({t.role for t in both[0].trends[:2]}, {"for", "against"})
        one = select_bets([tr(17.5, 9, 12, role="for")])
        self.assertTrue(one and not one[0].matching)

    def test_head_to_head_alone_never_makes_a_bet_or_a_match(self):
        h2h = [tr(17.5, 6, 6, role="for", scope="h2h_all"), tr(17.5, 6, 6, role="against", scope="h2h_all")]
        self.assertEqual(select_bets(h2h), [])
        b = select_bets([tr(17.5, 9, 12, role="for"), tr(17.5, 6, 6, role="against", scope="h2h_all")])
        self.assertFalse(b[0].matching)                       # h2h cannot be the second opinion
        self.assertEqual(len(b[0].trends), 2)                 # but it still shows as support

    def test_general_needs_seven_of_ten_or_better(self):
        self.assertEqual(select_bets([tr(17.5, 5, 8)]), [])                   # 5/8 is fine in a match, not alone
        self.assertEqual(select_bets([tr(17.5, 6, 10)]), [])                  # 60%
        self.assertEqual(len(select_bets([tr(17.5, 7, 10)])), 1)              # 7/10 is welcome
        self.assertEqual(select_bets([tr(17.5, 7, 8)]), [])                   # too few games

    def test_five_of_eight_is_enough_when_both_sides_agree(self):
        b = select_bets([tr(17.5, 5, 8, role="for"), tr(17.5, 5, 8, role="against", scope="form_away")])
        self.assertTrue(b and b[0].matching)

    # ---- ladders ---------------------------------------------------------------
    def ladder(self, lines, direction="over", **kw):
        out = []
        for line, hits, window in lines:
            out += [tr(line, hits, window, direction=direction, role="for", **kw),
                    tr(line, hits, window, direction=direction, role="against", scope="form_away", **kw)]
        return out

    def test_a_ladder_shows_only_its_best_line(self):
        lines = [(13.5, 11, 12), (14.5, 10, 12), (15.5, 9, 12), (16.5, 8, 12), (17.5, 7, 12)]
        got = [b.line for b in select_bets(self.ladder(lines))]
        self.assertEqual(got, [13.5])                         # the best hit rate, nothing else
        self.assertEqual(len(got), S.LADDER_MAX_LINES)

    def test_unders_also_show_one_line(self):
        lines = [(18.5, 7, 10), (19.5, 8, 10), (20.5, 9, 10), (21.5, 10, 10)]
        got = [b.line for b in select_bets(self.ladder(lines, direction="under"))]
        self.assertEqual(got, [21.5])

    def test_equal_hit_rates_pick_the_more_aggressive_line(self):
        got = [b.line for b in select_bets(self.ladder([(15.5, 9, 12), (16.5, 9, 12)]))]
        self.assertEqual(got, [16.5])

    def test_bets_in_a_category_run_from_most_to_least_relevant(self):
        # same hit rate, but the model likes 14.5 more than 16.5 -> 14.5 first
        a = self.ladder([(16.5, 9, 12)], about="home", subject="A", model_p=0.45)
        b = self.ladder([(14.5, 9, 12)], about="away", subject="B", model_p=0.68)
        got = [x.line for x in select_bets(a + b)]
        self.assertEqual(got, [14.5, 16.5])
        # a big sample beats a tiny perfect one when the model agrees
        big = self.ladder([(16.5, 28, 30)], about="home", subject="A")
        small = self.ladder([(14.5, 8, 8)], about="away", subject="B")
        self.assertEqual([x.line for x in select_bets(big + small)], [16.5, 14.5])

    def test_a_ladder_is_not_shown_twice_as_matching_and_general(self):
        matching = self.ladder([(11.5, 9, 12)])                             # both sides back 11.5
        general = [tr(13.5, 9, 10, role="for", scope="form_all")]           # one side only backs 13.5
        bets = select_bets(matching + general)
        self.assertEqual([b.line for b in bets], [11.5])

    def test_no_unders_for_shots_and_shots_on_target(self):
        from engine.trends.markets import build_catalog
        for mk in build_catalog():
            if mk.stat in S.NO_UNDER_STATS and mk.line is not None:
                self.assertEqual(mk.direction, "over", mk.key)
        self.assertTrue(any(mk.key == "team:corners:under:4.5" for mk in build_catalog()))   # other stats keep unders

    def test_chalk_cap_is_per_market(self):
        from engine.trends.select import chalk_cap
        self.assertEqual(chalk_cap("shots", "over", "home"), 0.70)
        self.assertEqual(chalk_cap("corners", "under", "home"), 0.70)     # a team's corners under: tight
        self.assertEqual(chalk_cap("corners", "under", "total"), 0.83)    # match total under: looser
        self.assertEqual(chalk_cap("goals", "over", "home"), S.CHALK_MAX_P)
        # the cases the bookmaker does not offer (model chance 81-89%) are dropped
        for stat, p, d in (("shots", 0.89, "over"), ("sot", 0.89, "over"), ("corners", 0.87, "over"), ("corners", 0.81, "under")):
            t = [tr(11.5, 9, 12, role="for", stat=stat, direction=d, model_p=p),
                 tr(11.5, 9, 12, role="against", scope="form_away", stat=stat, direction=d, model_p=p)]
            self.assertEqual(select_bets(t), [], f"{stat} {d} {p}")
        # lines the bookmaker does offer stay: shots over 56%, a team's corners under 66%
        for stat, p, d in (("shots", 0.56, "over"), ("corners", 0.66, "under")):
            ok = [tr(8.5, 9, 12, role="for", stat=stat, direction=d, model_p=p),
                  tr(8.5, 9, 12, role="against", scope="form_away", stat=stat, direction=d, model_p=p)]
            self.assertEqual(len(select_bets(ok)), 1, f"{stat} {d}")

    def test_the_kept_line_is_the_best_hit_rate_not_the_highest_line(self):
        odd = select_bets(self.ladder([(15.5, 9, 12), (16.5, 8, 12), (17.5, 7, 10)]))
        self.assertEqual([b.line for b in odd], [15.5])

    def test_counts_not_percentages(self):
        d = select_bets(self.ladder([(16.5, 9, 12)]))[0].to_dict()
        self.assertEqual((d["trends"][0]["hits"], d["trends"][0]["window"]), (9, 12))
        self.assertNotIn("p", d)
        self.assertNotIn("base_rate", d)
        self.assertEqual(d["fair"], 17.0)

    def test_ladder_cap_does_not_hide_other_ladders(self):
        a = self.ladder([(16.5, 9, 12)], about="home")
        b = self.ladder([(8.5, 9, 12)], about="away", subject="B")
        self.assertEqual(len(select_bets(a + b)), 2)

    # ---- no contradictions -----------------------------------------------------
    def test_no_contradictions(self):
        both_ways = (self.ladder([(16.5, 9, 12)]) + self.ladder([(17.5, 9, 12)], direction="under"))
        bets = select_bets(both_ways)
        for i, a in enumerate(bets):
            for b in bets[i + 1:]:
                self.assertFalse(conflicts(a, b), f"{a.label} vs {b.label}")

    def test_conflict_rules(self):
        def bet(kind, stat, direction, line, about):
            return Bet("k", "g", "l", about, kind, stat, direction, line, 0.8)
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

    def test_sections_are_ordered_matching_first_then_by_category(self):
        trends = (self.ladder([(16.5, 9, 12)]) + [tr(1.5, 8, 10, stat="goals", group="goals", about="away", subject="B")])
        flags = [b.matching for b in select_bets(trends)]
        self.assertEqual(flags, sorted(flags, reverse=True))


def matching_history():
    """A wins every home game; B loses every away game; A has 6 shots on target at home."""
    ms = season_history()
    d = date(2026, 9, 1)
    for i in range(12):
        d += timedelta(days=3)
        ms.append(mk(d, "C", "B", ft=(2, 0), ht=(1, 0), co_h=6, co_a=3, yc_h=1, yc_a=2, sot_h=7, sot_a=2))
    for m in ms:
        if m.home == "A":
            m.sot_h, m.sot_a = 6, 2
        elif m.sot_h is None:
            m.sot_h, m.sot_a = 3, 3
    return ms


class TestMatching(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        patcher = patch.object(S, "MATCH_MAX", 1000)          # lift the display cap for these checks
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = TrendEngine({"la_liga": matching_history()})
        self.raw = self.engine.trends_for_fixture(fx())

    def test_allowed_trends_exist_for_every_stat_and_results(self):
        keys = {t.market for t in self.raw if t.kind == "against"}
        self.assertIn("against:win:goals", keys)                          # B lost
        self.assertIn("against:goals:over:0.5", keys)                     # B conceded
        self.assertTrue(any(k.startswith("against:sot:over") for k in keys))   # B allowed shots on target

    def test_allowed_trend_text_and_merge_with_the_other_teams_bet(self):
        lost = find(self.raw, market="against:win:goals", subject="B", scope="form_away")[0]
        self.assertEqual(lost.text, "B lost in last 12 away games (La Liga)")
        own = find(self.raw, market="win:goals", subject="A", scope="form_home")[0]
        self.assertEqual(lost.bet_key, own.bet_key)                       # the same bet: "A to win"
        self.assertEqual((lost.about, lost.bet, lost.role, own.role), ("home", "A to win", "against", "for"))

    def test_conceded_goals_merge_into_the_team_goals_bet(self):
        keys = {t.bet_key for t in self.raw if t.market == "against:goals:under:0.5"}
        self.assertTrue(all(k.startswith("team:goals:under:0.5|") for k in keys))

    def test_result_bet_is_matching_when_both_sides_agree(self):
        bets = select_bets(self.raw)
        win = [b for b in bets if b.label == "A to win"][0]
        self.assertTrue(win.matching)
        self.assertEqual({t.role for t in win.trends[:2]}, {"for", "against"})
        flags = [b.matching for b in bets]
        self.assertTrue(flags[0])
        self.assertEqual(flags, sorted(flags, reverse=True))              # matching bets come first

    def test_shots_on_target_matching(self):
        sot = [b for b in select_bets(self.raw) if b.stat == "sot" and b.matching and b.about == "home"]
        self.assertTrue(sot, "A's own SOT form + B allowing SOT should match")
        self.assertEqual({t.role for t in sot[0].trends if t.scope.startswith("form")}, {"for", "against"})

    def test_only_one_strong_side_is_not_matching(self):
        ms = season_history()                                              # B now WINS its away games
        d = date(2026, 9, 1)
        for i in range(12):
            d += timedelta(days=3)
            ms.append(mk(d, "C", "B", ft=(0, 3), ht=(0, 1)))
        win = [b for b in select_bets(TrendEngine({"la_liga": ms}).trends_for_fixture(fx()))
               if b.label == "A to win"]
        self.assertTrue(win and not win[0].matching)

    def test_totals_match_when_both_teams_form_agree(self):
        tot = [b for b in select_bets(self.raw) if b.about == "total" and b.matching]
        for b in tot:
            self.assertEqual({t.role for t in b.trends[:2]}, {"home", "away"})


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
            with open(os.path.join(tmp, "index.html"), encoding="utf-8") as fh:
                html = fh.read()
            self.assertIn("A v B", html)
            self.assertIn("21:00", html)                      # 13:00 UTC shown in GMT+8
            self.assertIn("A to win", html)
            with open(os.path.join(tmp, "t.json"), encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["fixtures"][0]["home"], "A")

    def test_name_aliases_from_real_run_resolve(self):
        from engine.data.teams import TeamResolver
        bl = TeamResolver("bundesliga", {"FC Koln", "Hamburg", "Dortmund"})
        self.assertEqual(bl.resolve("FC Cologne"), "FC Koln")
        self.assertEqual(bl.resolve("Hamburger SV"), "Hamburg")
        self.assertEqual(bl.resolve("Borussia Dortmund"), "Dortmund")
        for canon in ("La Coruna", "Deportivo"):                    # whichever spelling the data uses
            self.assertEqual(TeamResolver("la_liga", {canon, "Barcelona"}).resolve("Deportivo"), canon)
        self.assertIsNone(TeamResolver("la_liga", {"Barcelona"}).resolve("Deportivo"))   # never invented

    def test_display_names_on_page_but_canonical_in_data(self):
        from engine.trends.names import display
        from engine.trends.page import render_page
        self.assertEqual(display("Ath Madrid"), "Atlético Madrid")
        self.assertEqual(display("Barcelona"), "Barcelona")
        f = {"league": "la_liga", "date": "2026-11-22", "kickoff": None, "home": "Ath Madrid",
             "away": "Celta", "raw_trend_count": 0, "bets": []}
        html = render_page({"fixtures": [f], "skipped": []}, self.NOW)
        self.assertIn("Atlético Madrid v Celta Vigo", html)

    def test_page_light_default_with_dark_toggle_and_skipped_note(self):
        from engine.trends.page import render_page
        html = render_page({"fixtures": [], "skipped": [{"league": "epl", "home": "X", "away": "Y",
                                                         "reason": "team name not matched"}]},
                           self.NOW, {"epl": "2026-11-21"})
        self.assertIn('id="theme"', html)
        self.assertIn("Dark mode", html)
        self.assertNotIn("prefers-color-scheme", html)            # light unless the button is clicked
        self.assertIn("21/11/2026", html)
        self.assertIn("Not shown (team name not matched): X v Y", html)

    def test_updated_label_is_live_and_stale_warning_is_20_hours(self):
        from engine.trends.page import render_page
        html = render_page({"fixtures": [], "skipped": []}, self.NOW)
        self.assertIn("min ago", html)                       # "Updated 12 min ago", not "less than an hour"
        self.assertNotIn("less than an hour", html)
        self.assertIn('data-hours="20"', html)               # warning only after 20 hours
        self.assertIn("setInterval(tick", html)              # keeps counting while the tab stays open

    def test_page_has_matching_section_first_then_general(self):
        from engine.trends.page import render_page
        def bet(label, matching):
            return {"group": "goals", "label": label, "about": "home", "stat": "goals", "direction": "over",
                    "line": 0.5, "matching": matching, "fair": 1.7,
                    "trends": [{"text": "X scored in last 8 games (La Liga)", "hits": 8, "window": 8,
                                "scope": "form_all", "subject": "X", "role": "for"}]}
        f = {"league": "serie_a", "date": "2026-11-22", "kickoff": None, "home": "Juventus", "away": "Venezia",
             "raw_trend_count": 9, "bets": [bet("Juventus to score", True), bet("Venezia goals Under 2.5", False)]}
        html = render_page({"fixtures": [f], "skipped": []}, self.NOW)
        self.assertLess(html.index("Highest matching trends"), html.index("General trends"))
        self.assertLess(html.index("Juventus to score"), html.index("General trends"))
        self.assertGreater(html.index("Venezia goals Under 2.5"), html.index("General trends"))
        self.assertIn("1 matching", html)
        self.assertIn("fair line", html)
        self.assertNotIn("league avg", html)

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
            self.assertGreaterEqual(t.hits / t.window, S.MIN_HIT_RATE)
            self.assertGreaterEqual(t.window, 5)
        bets_a, bets_b = select_bets(a), select_bets(b)
        self.assertEqual([x.label for x in bets_a], [x.label for x in bets_b])
        for x in bets_a:
            if x.fair is not None:
                self.assertGreater(x.fair, 0)


if __name__ == "__main__":
    unittest.main()
