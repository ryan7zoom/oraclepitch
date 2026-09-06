# Oraclepitch

Automated Premier League prediction engine: Dixon-Coles + Monte Carlo for
match outcomes and goals, Negative Binomial models for shots on target
and corners, a Kelly Criterion backtester, and a static HTML dashboard
published via GitHub Pages.

## Status (read this before trusting any output)

**Working and tested (65 tests across 13 files, all passing):**
- Match Winner, Double Chance (1X/X2 only), Match Total Goals, Team
  Total Goals, Team Shots on Target (Over-only), Match Shots on Target
  (Over-only) — all live via the daily GitHub Actions workflow.
- A working backtest with **real Bet365 historical odds** for
  match_winner and match_total_goals (Over/Under 2.5 only — that's the
  only goals line the data source prices).
- A corners model, backtestable and calibration-checked against real
  historical results — but **not enabled for live daily predictions**
  (see below).

**Known gaps, not hidden:**
- **Corners are disabled in live predictions** (`config.BET_TYPES["team_corners"] = False`).
  No free, reliable, live data source for upcoming fixtures' corners
  was found. `engine/prediction/corners.py` works and is backtestable
  using football-data.co.uk's historical corners data, but that source
  is a static archive, not a live API — it can't power tomorrow's
  predictions.
- **No real odds exist for**: double chance, team total goals, and
  shots on target markets. The backtest correctly reports zero bets for
  these (not a bug — there's simply no odds data to evaluate against).

Every one of these gaps is also documented directly in the relevant
module's docstring, not just here.

## Setup

1. Get a free API-Football key at [api-football.com](https://www.api-football.com/)
   or via RapidAPI (free tier: 100 requests/day). No key is needed for
   the backtest — that uses football-data.co.uk, which requires no
   signup.
2. Add the key as a GitHub Actions secret named `API_FOOTBALL_KEY` in
   your repo's Settings → Secrets and variables → Actions.
3. Enable GitHub Pages for this repo, set to serve from the `docs/`
   folder on your default branch.
4. Run the "Run Backtest" workflow manually first (Actions tab →
   workflow_dispatch) to sanity-check the model against historical
   data before enabling daily predictions.
5. The "Daily EPL Predictions" workflow runs automatically twice a day
   (04:00 and 12:00 UTC) or can be triggered manually.

## Local testing

```bash
pip install -r requirements.txt
python3 tests/test_dixon_coles.py   # or any tests/test_*.py file
```

Each test file can be run standalone and prints `PASS:` lines for every
check, ending in `All tests passed.` if everything's green.

## Project layout

```
config.py                      All tunable parameters
engine/
  sources/                     Data sources (API-Football, football-data.co.uk)
  prediction/                  Dixon-Coles, Monte Carlo, shots, corners, ensemble
  backtest/                    Kelly criterion, backtest simulator + CLI
  output/                      HTML dashboard generator
  main.py                      Daily live-prediction entry point
tests/                         One test file per module, runnable standalone
.github/workflows/             Daily predictions + on-demand backtest
docs/                          GitHub Pages output (generated, not hand-edited)
data/                          Cached historical stats + latest predictions JSON
```

## A note on the backtest numbers

If you run the backtest and see an ROI figure, that reflects real
historical Bet365 odds for match_winner and match_total_goals only.
Don't read a positive ROI here as "this will make money" without
understanding: backtests overfit easily, past odds don't predict future
odds, and this covers only 2 of the 7 originally-requested bet types.
Treat it as a sanity check on the model's basic soundness, not a
green light.
