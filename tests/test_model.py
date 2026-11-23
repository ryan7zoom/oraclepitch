"""tests/test_model.py - offline tests for engine/model/."""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.data.espn import EspnEvent
from engine.data.model import Match
from engine.data.store import Store
from engine.model import dist, ledger, markets, render, sim
from engine.model.ratings import LeagueModel, Params, promoted_priors
from engine.model.run import collect_fixtures


def params(**kw):
    mu = {"g_h": 1.8, "g_a": 0.9, "c_h": 6.2, "c_a": 4.1, "s_h": 14, "s_a": 9, "t_h": 5, "t_a": 3}
    mu.update(kw)
    return Params(mu=mu, share={"h": .45, "a": .44}, phi={"g": 1.0, "c": 1.4, "s": 1.6, "t": 1.2},
                  n_hist={"h": 30, "a": 30})


class TestDist(unittest.TestCase):
    def test_pmf_mean_and_dispersion(self):
        for mu, phi, n in ((1.4, 1.0, 16), (6.0, 1.4, 36), (13.0, 1.6, 70)):
            p = dist.pmf(mu, phi, n)
            k = np.arange(len(p))
            mean = (p * k).sum(); var = (p * (k - mean) ** 2).sum()
            self.assertAlmostEqual(p.sum(), 1.0, places=9)
            self.assertAlmostEqual(mean, mu, delta=0.03 * mu)
            self.assertAlmostEqual(var / mean, max(phi, 1.0), delta=0.06)

    def test_over_and_conv(self):
        p = dist.pmf(2.0, 1.0, 16)
        self.assertAlmostEqual(dist.p_over(p, 1.5), 1 - p[0] - p[1])
        self.assertAlmostEqual(dist.conv(p, p).sum(), 1.0)


class TestMarkets(unittest.TestCase):
    def test_probabilities_are_consistent(self):
        pr = markets.probs(params())
        self.assertEqual(len(pr), len(markets.all_keys()))
        for pre in ("res", "h1", "h2"):
            self.assertAlmostEqual(pr[f"{pre}:H"] + pr[f"{pre}:D"] + pr[f"{pre}:A"], 1.0, places=6)
        self.assertAlmostEqual(pr["dc:1X"], pr["res:H"] + pr["res:D"], places=9)
        for k in markets.all_keys():
            if ":O" in k:
                self.assertAlmostEqual(pr[k] + pr[k.replace(":O", ":U")], 1.0, places=9)
        self.assertGreater(pr["res:H"], pr["res:A"])
        self.assertLess(pr["g:tot:O3.5"], pr["g:tot:O2.5"])
        self.assertAlmostEqual(pr["btts:Y"], (1 - np.exp(-1.8)) * (1 - np.exp(-0.9)), places=3)

    def test_eval_key(self):
        v = {"g_h": 3, "g_a": 2, "g1_h": 1, "g1_a": 1, "g2_h": 2, "g2_a": 1, "c_h": 5, "c_a": 7,
             "s_h": 12, "s_a": 10, "t_h": 4, "t_a": 3}
        self.assertTrue(markets.eval_key("res:H", v))
        self.assertTrue(markets.eval_key("h1:D", v))
        self.assertTrue(markets.eval_key("h2:H", v))
        self.assertTrue(markets.eval_key("dc:1X", v))
        self.assertTrue(markets.eval_key("btts:Y", v))
        self.assertTrue(markets.eval_key("g:tot:O4.5", v))
        self.assertTrue(markets.eval_key("g:h:U3.5", v))
        self.assertFalse(markets.eval_key("c:tot:U11.5", v))      # 12 corners
        self.assertTrue(markets.eval_key("t:a:U3.5", v))
        v["c_h"] = None
        self.assertIsNone(markets.eval_key("c:tot:O9.5", v))

    def test_actuals_from_match(self):
        m = Match("epl", 2026, date(2026, 11, 8), "A", "B", ft_h=2, ft_a=1, ht_h=1, ht_a=0, co_h=6, co_a=3)
        a = markets.actuals(m)
        self.assertEqual(a["res:H"], 1)
        self.assertEqual(a["h1:H"], 1)
        self.assertEqual(a["h2:D"], 1)         # 2nd half 1-1
        self.assertEqual(a["c:tot:O8.5"], 1)
        self.assertNotIn("s:tot:O20.5", a)     # shots unknown -> not graded


def synth(n_seasons=4, seed=1):
    """Synthetic league: low-numbered teams are strong; three teams are 'promoted' in 2023."""
    rnd = random.Random(seed)
    out = []
    for season in range(2021, 2021 + n_seasons):
        teams = [f"T{i}" for i in range(20)] if season < 2023 else [f"T{i}" for i in range(3, 23)]
        d0 = date(season, 8, 10)
        k = 0
        for a in teams:
            for b in teams:
                if a == b:
                    continue
                k += 1
                sa = 1.7 - 0.04 * int(a[1:]); sb = 1.7 - 0.04 * int(b[1:])
                gh = np.random.default_rng(rnd.randint(0, 10**6)).poisson(max(0.3, sa * 0.9))
                ga = np.random.default_rng(rnd.randint(0, 10**6)).poisson(max(0.3, sb * 0.7))
                out.append(Match("epl", season, d0 + timedelta(days=k // 10), a, b, ft_h=int(gh), ft_a=int(ga),
                                 ht_h=int(gh // 2), ht_a=int(ga // 2), co_h=5 + int(gh), co_a=4 + int(ga),
                                 sh_h=10 + 2 * int(gh), sh_a=8 + 2 * int(ga), sot_h=3 + int(gh), sot_a=2 + int(ga)))
    return sorted(out, key=lambda m: m.date)


class TestRatings(unittest.TestCase):
    def test_strong_team_gets_higher_expectation_and_early_shrinkage(self):
        ms = synth()
        model = LeagueModel()
        for m in ms:
            model.add(m)
        strong = model.params_for("T1", "T18")
        weak = model.params_for("T18", "T1")
        self.assertGreater(strong.mu["g_h"], weak.mu["g_h"])
        self.assertGreater(strong.mu["g_h"], strong.mu["g_a"])
        # a brand-new team with no games is pulled to the league average, not to an extreme
        new = model.params_for("NEWTEAM", "T10")
        self.assertTrue(0.4 < new.mu["g_h"] < 3.0)
        self.assertEqual(new.n_hist["h"], 0)

    def test_promoted_priors_found(self):
        pri = promoted_priors(synth(), before_season=2025)
        self.assertIn("g", pri)
        att, deff = pri["g"]
        self.assertTrue(0.3 < att < 2.0 and 0.3 < deff < 2.0)


class TestSim(unittest.TestCase):
    def test_marginals_halves_and_correlation(self):
        p = params()
        corr = np.eye(8)
        i, j = markets.BASE_VARS.index("c_h"), markets.BASE_VARS.index("s_h")
        corr[i, j] = corr[j, i] = 0.6
        s = sim.simulate(p, corr, n=6000, seed=3).astype(float)
        col = {c: k for k, c in enumerate(markets.SIM_COLS)}
        self.assertAlmostEqual(s[:, col["g_h"]].mean(), 1.8, delta=0.08)
        self.assertAlmostEqual(s[:, col["c_h"]].mean(), 6.2, delta=0.25)
        self.assertTrue((s[:, col["g1_h"]] <= s[:, col["g_h"]]).all())
        self.assertGreater(np.corrcoef(s[:, col["c_h"]], s[:, col["s_h"]])[0, 1], 0.35)
        # joint of one leg ~ exact probability
        pr = markets.probs(p)
        self.assertAlmostEqual(sim.joint_prob(s.astype(np.uint8)[:3000], ["res:H"]), pr["res:H"], delta=0.04)
        # two positively linked legs together are likelier than if independent
        k1, k2 = "c:h:O6.5", "s:h:O14.5"
        both = sim.joint_prob(s.astype(np.uint8), [k1, k2])
        self.assertGreater(both, pr[k1] * pr[k2])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_javascript_matches_python(self):
        s = sim.simulate(params(), None, n=400, seed=5)
        keys = ["res:H", "dc:X2", "h1:D", "h2:A", "btts:Y", "g:tot:O2.5", "g1:h:O0.5", "g2:a:U1.5",
                "c:tot:O9.5", "s:h:U14.5", "t:a:O2.5"]
        script = ("const atob=b=>Buffer.from(b,'base64').toString('binary');"
                  "const document={addEventListener(){},getElementById:()=>null,documentElement:{getAttribute:()=>null},querySelector:()=>({dataset:{sim:process.argv[1]}})};"
                  + render.JS + "const keys=JSON.parse(process.argv[2]);"
                  "console.log(JSON.stringify(keys.map(k=>joint(0,[k])).concat([joint(0,keys.slice(0,3))])));")
        r = subprocess.run(["node", "-e", script, sim.encode(s), json.dumps(keys)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        py = [sim.joint_prob(s, [k]) for k in keys] + [sim.joint_prob(s, keys[:3])]
        for a, b in zip(js, py):
            self.assertAlmostEqual(a, b, places=9)


class TestLedger(unittest.TestCase):
    def test_save_freeze_and_grade(self):
        p = params()
        d = date(2026, 11, 22)
        rows = ledger.upsert([], [(d, "epl", "A", "B", p)], d, "t1")
        self.assertEqual(len(rows), 1)
        p2 = params(g_h=2.5)
        ledger.upsert(rows, [(d, "epl", "A", "B", p2)], d, "t2")          # same day: refreshed
        self.assertEqual(rows[0]["made_on"], "t2")
        ledger.upsert(rows, [(d, "epl", "A", "B", params(g_h=0.5))], d + timedelta(days=1), "t3")   # past: frozen
        self.assertEqual(rows[0]["made_on"], "t2")
        live = ledger.new_live()
        res = Match("epl", 2026, d, "A", "B", ft_h=2, ft_a=1, ht_h=1, ht_a=0, co_h=6, co_a=4, sh_h=12, sh_a=8, sot_h=5, sot_a=3)
        self.assertEqual(ledger.grade(rows, lambda *a: None, live), 0)     # not finished yet
        self.assertEqual(ledger.grade(rows, lambda lg, h, a, dd: res, live), 1)
        self.assertEqual(ledger.grade(rows, lambda lg, h, a, dd: res, live), 0)   # never graded twice
        self.assertEqual(live["graded_matches"], 1)
        self.assertGreater(sum(b[0] for b in live["buckets"]), 100)


class TestFixturesAndRender(unittest.TestCase):
    def _models(self):
        ms = synth()
        st = Store("epl", os.path.join(tempfile.mkdtemp(), "epl.csv"))
        for m in ms:
            st.upsert_fd(m)
        model = LeagueModel()
        for m in ms:
            model.add(m)
        return {"epl": model}, {"epl": st}

    def test_collect_fixtures_uses_espn_and_skips_started_finished_unmatched(self):
        models, stores = self._models()
        now = datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)          # 11:00 GMT+8
        today_local = date(2026, 11, 22)
        K = lambda h: datetime(2026, 11, 22, h, 0, tzinfo=timezone.utc)

        class FakeEspn:
            def scoreboard(self, lg, day):
                if day != today_local:
                    return []
                return [EspnEvent("1", day, False, "T3", "T4", None, None, None, K(14)),     # upcoming: kept
                        EspnEvent("2", day, False, "T5", "T6", None, None, None, K(2)),      # already started
                        EspnEvent("3", day, True, "T7", "T8", 1, 0, None, K(1)),             # finished
                        EspnEvent("4", day, False, "Nowhere FC", "T9", None, None, None, K(15))]   # unmatched name
        fx, warn = collect_fixtures(models, stores, now, today_local, espn=FakeEspn())
        self.assertEqual([(f["home"], f["away"]) for f in fx], [("T3", "T4")])
        self.assertEqual(fx[0]["ko_dt"].strftime("%H:%M"), "22:00")      # 14:00 UTC = 22:00 GMT+8
        self.assertEqual(len(warn), 1)

    def test_render_page(self):
        p = params()
        pred = {"league_name": "Premier League", "kickoff": "22:00", "day": "Sun 22 Nov", "home_name": "Home <FC>",
                "away_name": "Away", "params": p, "probs": markets.probs(p), "base": {"res:H": 0.45},
                "thin": [("Away", 12)], "sim": sim.encode(sim.simulate(p, None, n=200))}
        card = {"markets": {"Goals": {"skill": .04, "calibration_gap": .002, "validated": True}},
                "pooled": {"buckets": [{"said": .85, "actual": .858, "n": 1000}]}, "matches_tested": 7415,
                "period": "x", "builder": {"n": 10, "avg_joint_model": .9, "avg_product_if_independent": .89, "actual_all_three_hit": .92}}
        page = render.render([pred], card, ledger.new_live(), "22 Nov 10:00", "2026-11-21", ["w"])
        self.assertIn("Home &lt;FC&gt;", page)                  # escaped
        self.assertIn('data-k="c:tot:O', page)
        self.assertIn("only 12 league games", page)
        self.assertIn("Does it work?", page)

    def test_sections_collapsed_and_odds_shown(self):
        import re
        p = params()
        pred = {"league_name": "Premier League", "kickoff": "22:00", "day": "Sun 22 Nov", "home_name": "H",
                "away_name": "A", "params": p, "probs": markets.probs(p), "base": {}, "thin": [],
                "sim": sim.encode(sim.simulate(p, None, n=200))}
        card = {"markets": {}, "pooled": {"buckets": []}, "matches_tested": 1, "period": "x",
                "builder": {"n": 1, "avg_joint_model": .9, "avg_product_if_independent": .89, "actual_all_three_hit": .9}}
        page = render.render([pred], card, ledger.new_live(), "22 Nov 10:00", "2026-11-21", [])
        self.assertIsNone(re.search(r"<details[^>]*\bopen\b", page))        # nothing opens by default
        self.assertGreaterEqual(page.count("<details class='sect'>"), 5)       # every section is collapsible
        n_btn = page.count('class="pb')
        self.assertEqual(len(re.findall(r"class='od'>@\d+\.\d\d", page)) >= n_btn, True)   # every option has odds
        self.assertEqual(render.odds(0.8), "1.25")
        self.assertEqual(render.odds(0.0), "100.00")


if __name__ == "__main__":
    unittest.main()


class TestSelfContained(unittest.TestCase):
    def test_new_code_does_not_import_old_modules(self):
        """The new system must keep working after the old engine files are deleted."""
        code = ("import sys\n"
                "for m in ('engine.kickoff','engine.sources','engine.sources.openfootball_source','engine.sources.base',"
                "'engine.streaks','engine.output','engine.main'): sys.modules[m]=None\n"
                "import engine.model.run, engine.model.backtest, engine.data.update, engine.data.teams\n"
                "print('ok')")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)


class TestNewAliases(unittest.TestCase):
    def test_names_from_the_first_live_run(self):
        from engine.data.teams import TeamResolver
        self.assertEqual(TeamResolver("la_liga", {"La Coruna", "Celta"}).resolve("Deportivo"), "La Coruna")
        self.assertEqual(TeamResolver("bundesliga", {"FC Koln", "Stuttgart"}).resolve("FC Cologne"), "FC Koln")
