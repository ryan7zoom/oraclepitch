"""
engine/backtest/simulator.py

Backtesting engine: walks through historical matches in chronological
order, fits the model using only data available before each match
("no lookahead"), generates predictions, and (if odds are supplied)
evaluates Kelly-staked bets against actual outcomes to compute ROI.

************************************************************************
ODDS COVERAGE STATUS (read before trusting backtest ROI numbers):
The backtest CLI (_cli_main() below) uses xgabora/Club-Football-Match-Data
(engine/sources/xgabora_match_data_source.py) - a free, no-key,
GitHub-hosted CSV covering 2000-present - for real historical odds.
This dataset does not document which specific bookmaker its odds
columns come from, so treat these as "a" bookmaker's real market odds,
not confirmed as Bet365 specifically. This covers match_winner
(home/draw/away) and match_total_goals at the Over/Under 2.5 line
specifically, which is the only goals line this data source provides
odds for.

NOTE: this replaces an earlier version of this module that used
football-data.co.uk directly. That source experienced an extended
outage (confirmed down via direct browser visit and curl from multiple
independent networks, not a bot-detection or rate-limit issue - see
git history for the diagnostic trail) and was replaced with this
GitHub-hosted alternative, which is far less likely to experience
similar downtime since it's served from GitHub's own infrastructure.

Double chance (1X/X2), team total goals, team shots on target, and
match shots on target still have NO real odds source connected. The
run_backtest() function below will correctly report zero bets for
those markets (via odds_provider returning None for them) rather than
fabricate numbers - this is intentional, not an oversight, and the CLI
report explicitly says so rather than silently showing "0% ROI" as if
that were a meaningful result.

This module's core run_backtest() function accepts odds via an
injectable OddsProvider specifically so this partial-coverage situation
is handled cleanly - callers decide per-market whether real odds exist,
rather than the simulator assuming they do or don't globally.
************************************************************************

No-lookahead discipline: for each match at date D, the model is re-fit
(or at minimum, only shown matches with date < D) using only matches
strictly before D. This is the single most important correctness
property of a backtest - getting it wrong (e.g. fitting once on the
whole season) silently produces unrealistically good results because
the model "sees the future" relative to earlier matches in the fit.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np

from engine.prediction.dixon_coles import DixonColesModel, MatchInput
from engine.prediction.monte_carlo import MonteCarloSimulator
from engine.backtest.kelly import kelly_fraction


# Type alias: given (home_team, away_team, match_date, market, selection),
# return decimal odds or None if unavailable. This is intentionally a
# plain callable rather than a class so a simple lookup dict/DataFrame
# can be wrapped in a lambda without extra boilerplate.
OddsProvider = Callable[[str, str, date, str, str], Optional[float]]


@dataclass
class BetRecord:
    match_date: date
    home_team: str
    away_team: str
    market: str
    selection: str
    model_probability: float
    decimal_odds: float
    stake_fraction: float
    won: bool
    profit_fraction: float  # profit as a fraction of bankroll, negative if lost


@dataclass
class BacktestReport:
    bets: list = field(default_factory=list)
    starting_bankroll: float = 1.0
    ending_bankroll: float = 1.0

    @property
    def total_bets(self) -> int:
        return len(self.bets)

    @property
    def win_rate(self) -> float:
        if not self.bets:
            return 0.0
        return sum(1 for b in self.bets if b.won) / len(self.bets)

    @property
    def roi(self) -> float:
        """Total profit / total amount staked (as fractions of bankroll)."""
        total_staked = sum(b.stake_fraction for b in self.bets)
        if total_staked == 0:
            return 0.0
        total_profit = sum(b.profit_fraction for b in self.bets)
        return total_profit / total_staked

    @property
    def average_odds(self) -> float:
        if not self.bets:
            return 0.0
        return sum(b.decimal_odds for b in self.bets) / len(self.bets)

    @property
    def max_drawdown(self) -> float:
        """Maximum peak-to-trough decline in bankroll over the sequence
        of bets, as a fraction (0.0 to 1.0).
        """
        if not self.bets:
            return 0.0
        bankroll = self.starting_bankroll
        peak = bankroll
        max_dd = 0.0
        for bet in self.bets:
            bankroll += bankroll * bet.profit_fraction
            peak = max(peak, bankroll)
            drawdown = (peak - bankroll) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)
        return max_dd

    def roi_by_market(self) -> dict:
        markets = {b.market for b in self.bets}
        result = {}
        for market in markets:
            market_bets = [b for b in self.bets if b.market == market]
            staked = sum(b.stake_fraction for b in market_bets)
            profit = sum(b.profit_fraction for b in market_bets)
            result[market] = {
                "roi": profit / staked if staked > 0 else 0.0,
                "n_bets": len(market_bets),
                "win_rate": sum(1 for b in market_bets if b.won) / len(market_bets) if market_bets else 0.0,
            }
        return result

    def roi_by_selection(self) -> dict:
        """Finer-grained breakdown than roi_by_market(): groups by
        (market, selection) pair rather than market alone.

        This exists because roi_by_market() alone can hide exactly
        where a market's losses are coming from - e.g. "match_winner"
        at -5.8% ROI could mean all three selections (home_win, draw,
        away_win) are mildly unprofitable, or it could mean one
        selection (e.g. draw, which is notoriously hard to price well)
        is badly unprofitable while the others are fine or even
        profitable, and the market-level average is masking that. This
        was specifically requested after an initial backtest run showed
        an overall loss, to identify whether the loss is uniform or
        concentrated - a very different diagnosis and a very different
        fix depending on which it is.
        """
        keys = {(b.market, b.selection) for b in self.bets}
        result = {}
        for market, selection in keys:
            sel_bets = [b for b in self.bets if b.market == market and b.selection == selection]
            staked = sum(b.stake_fraction for b in sel_bets)
            profit = sum(b.profit_fraction for b in sel_bets)
            avg_model_prob = sum(b.model_probability for b in sel_bets) / len(sel_bets)
            avg_odds = sum(b.decimal_odds for b in sel_bets) / len(sel_bets)
            avg_implied_prob = sum(1.0 / b.decimal_odds for b in sel_bets) / len(sel_bets)
            result[(market, selection)] = {
                "roi": profit / staked if staked > 0 else 0.0,
                "n_bets": len(sel_bets),
                "win_rate": sum(1 for b in sel_bets if b.won) / len(sel_bets) if sel_bets else 0.0,
                "avg_model_probability": avg_model_prob,
                "avg_implied_probability": avg_implied_prob,
                "avg_odds": avg_odds,
            }
        return result

    def summary(self) -> str:
        lines = [
            f"Total bets: {self.total_bets}",
            f"Win rate: {self.win_rate:.1%}",
            f"Overall ROI: {self.roi:.1%}",
            f"Average odds: {self.average_odds:.2f}",
            f"Max drawdown: {self.max_drawdown:.1%}",
            "",
            "ROI by market:",
        ]
        for market, stats in sorted(self.roi_by_market().items()):
            lines.append(
                f"  {market}: ROI={stats['roi']:+.1%}  "
                f"n={stats['n_bets']}  win_rate={stats['win_rate']:.1%}"
            )

        lines.append("")
        lines.append("ROI by selection (finer breakdown - where within each market the profit/loss is coming from):")
        for (market, selection), stats in sorted(self.roi_by_selection().items()):
            # avg_model_probability vs avg_implied_probability shows the
            # AVERAGE edge the model believed it had going into these
            # bets - if this gap was consistently positive but the
            # selection still lost money, that's a strong signal the
            # model's probabilities are systematically overconfident
            # for this specific selection, not just unlucky variance.
            edge = stats["avg_model_probability"] - stats["avg_implied_probability"]
            lines.append(
                f"  {market}/{selection}: ROI={stats['roi']:+.1%}  "
                f"n={stats['n_bets']}  win_rate={stats['win_rate']:.1%}  "
                f"avg_model_prob={stats['avg_model_probability']:.1%}  "
                f"avg_implied_prob={stats['avg_implied_probability']:.1%}  "
                f"avg_edge={edge:+.1%}  avg_odds={stats['avg_odds']:.2f}"
            )
        return "\n".join(lines)


def run_backtest(
    historical_matches: list[MatchInput],
    odds_provider: Optional[OddsProvider] = None,
    min_training_matches: int = 50,
    refit_every_n_matches: int = 10,
    monte_carlo_iterations: int = 5000,
    starting_bankroll: float = 1.0,
) -> BacktestReport:
    """Run a chronological, no-lookahead backtest.

    historical_matches: ALL matches to backtest over, in any order (will
        be sorted internally by date).
    odds_provider: callable returning decimal odds for a given bet, or
        None to skip odds-dependent bet evaluation entirely (in which
        case the report will have zero bets - see module docstring on
        why real odds aren't wired in yet).
    min_training_matches: don't start betting until at least this many
        historical matches are available to fit on, to avoid absurdly
        unstable early-season model fits.
    refit_every_n_matches: re-fit the model every N matches rather than
        after every single one, since re-fitting via MLE optimization
        is the most expensive step - refitting after every match is
        needlessly slow without materially changing accuracy over a
        short window. This is a documented approximation, not an
        oversight - refitting after literally every match is arguably
        more "pure" no-lookahead, but 10-match windows introduce
        negligible additional lookahead risk relative to the (large)
        performance cost of always refitting.
    """
    if odds_provider is None:
        return BacktestReport(bets=[], starting_bankroll=starting_bankroll,
                               ending_bankroll=starting_bankroll)

    sorted_matches = sorted(historical_matches, key=lambda m: m.match_date)
    bets = []
    bankroll = starting_bankroll

    dc_model = DixonColesModel()
    fitted_up_to_index = -1

    for i, match in enumerate(sorted_matches):
        if i < min_training_matches:
            continue

        training_data = sorted_matches[:i]  # strictly before this match - no lookahead

        needs_refit = (
            fitted_up_to_index < 0
            or (i - fitted_up_to_index) >= refit_every_n_matches
        )
        if needs_refit:
            try:
                dc_model.fit(training_data, as_of=training_data[-1].match_date)
                fitted_up_to_index = i
            except Exception:
                # Fitting can fail on pathological small samples - skip
                # this match rather than crash the whole backtest.
                continue

        try:
            outcome_probs = dc_model.match_outcome_probs(match.home_team, match.away_team)
            dc_probs = dc_model.double_chance_probs(match.home_team, match.away_team)
            goals_probs = dc_model.match_total_goals_over_probs(match.home_team, match.away_team)
        except ValueError:
            # Unknown team (e.g. newly promoted, not seen in training window)
            continue

        actual_home_win = match.home_goals > match.away_goals
        actual_draw = match.home_goals == match.away_goals
        actual_away_win = match.home_goals < match.away_goals
        actual_total_goals = match.home_goals + match.away_goals

        candidates = [
            ("match_winner", "home_win", outcome_probs["home_win"], actual_home_win),
            ("match_winner", "draw", outcome_probs["draw"], actual_draw),
            ("match_winner", "away_win", outcome_probs["away_win"], actual_away_win),
            ("double_chance", "1X", dc_probs["1X"], actual_home_win or actual_draw),
            ("double_chance", "X2", dc_probs["X2"], actual_away_win or actual_draw),
        ]
        for line, key in [(0.5, "over_0.5"), (1.5, "over_1.5"), (2.5, "over_2.5"),
                          (3.5, "over_3.5"), (4.5, "over_4.5")]:
            candidates.append((
                "match_total_goals", f"over_{line}", goals_probs[key],
                actual_total_goals > line,
            ))

        for market, selection, model_prob, won in candidates:
            odds = odds_provider(match.home_team, match.away_team, match.match_date, market, selection)
            if odds is None:
                continue

            kelly = kelly_fraction(model_prob, odds)
            if not kelly.should_bet:
                continue

            profit_fraction = (
                kelly.recommended_fraction * (odds - 1) if won
                else -kelly.recommended_fraction
            )
            bets.append(BetRecord(
                match_date=match.match_date, home_team=match.home_team,
                away_team=match.away_team, market=market, selection=selection,
                model_probability=model_prob, decimal_odds=odds,
                stake_fraction=kelly.recommended_fraction, won=won,
                profit_fraction=profit_fraction,
            ))
            bankroll += bankroll * profit_fraction

    return BacktestReport(bets=bets, starting_bankroll=starting_bankroll, ending_bankroll=bankroll)


def _cli_main():
    """CLI entry point for `python -m engine.backtest.simulator --start Y --end Y`,
    matching the GitHub Actions backtest workflow's invocation.

    Uses xgabora/Club-Football-Match-Data (engine/sources/xgabora_match_data_source.py)
    for BOTH the goals data used to fit Dixon-Coles AND the historical
    odds needed for Kelly staking - this is the odds source that was
    previously missing (see this module's earlier docstring section
    "ODDS COVERAGE STATUS"). That gap is now closed for the match_winner
    and match_total_goals (Over/Under 2.5 only - see note below) markets.
    Double chance, team totals, and shots on target markets still have
    no real odds source and will report zero bets for those markets
    specifically - this is called out in the report, not hidden.
    """
    import argparse
    import logging
    import os
    import time

    import config
    from engine.sources.xgabora_match_data_source import XgaboraMatchDataSource

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("engine.backtest.simulator")

    parser = argparse.ArgumentParser(description="Run a historical backtest")
    parser.add_argument("--start", type=int, default=config.BACKTEST_START_SEASON)
    parser.add_argument("--end", type=int, default=config.BACKTEST_END_SEASON)
    args = parser.parse_args()

    source = XgaboraMatchDataSource()
    all_matches = []
    all_corners_rows = []
    # odds_lookup maps (home_team, away_team, match_date) -> HistoricalMatchOdds,
    # used by the odds_provider closure below. Keyed on the triple rather
    # than just team names since the same fixture (e.g. Arsenal vs Chelsea)
    # recurs across seasons.
    odds_lookup = {}

    for season in range(args.start, args.end + 1):
        # Small delay between seasons - the previous back-to-back
        # fetch pattern (5 seasons in under 3 seconds) triggered
        # consistent 503s from football-data.co.uk on every attempt,
        # consistent with a request-rate trigger rather than a hard
        # per-IP block. Spacing requests out is a low-cost thing to
        # try before concluding the site has blocked GitHub Actions
        # outright (see fetch_season()'s retry logic for the other
        # half of this mitigation).
        if season > args.start:
            time.sleep(5)

        logger.info(f"Fetching season {season} from xgabora/Club-Football-Match-Data...")
        try:
            season_results = source.fetch_season(season)
        except Exception as e:
            logger.error(f"Failed to fetch season {season}: {e}")
            continue

        with_goals = [r for r in season_results if r.home_goals is not None and r.away_goals is not None]
        with_odds = [r for r in with_goals if r.odds_home_win is not None]
        with_corners = [r for r in season_results if r.home_corners is not None and r.away_corners is not None]
        logger.info(
            f"  {len(season_results)} rows, {len(with_goals)} with goals, "
            f"{len(with_odds)} with usable match-winner odds, "
            f"{len(with_corners)} with corners data"
        )

        for r in with_goals:
            all_matches.append(MatchInput(r.home_team, r.away_team, r.home_goals, r.away_goals, r.date))
            odds_lookup[(r.home_team, r.away_team, r.date)] = r
        all_corners_rows.extend(with_corners)

    logger.info(f"Total matches across seasons {args.start}-{args.end}: {len(all_matches)}")

    if len(all_matches) < 50:
        logger.error(
            f"Only {len(all_matches)} matches fetched - insufficient for a "
            f"meaningful backtest. Check the season range or xgabora/Club-Football-Match-Data "
            f"availability for these seasons."
        )

    def odds_provider(home_team, away_team, match_date, market, selection):
        """Look up real historical odds for a given bet (bookmaker
        unspecified by the source dataset - see module docstring).
        Returns None for markets/selections this data source doesn't
        cover (double_chance, team_total_goals, shots_on_target), which
        correctly causes run_backtest() to skip evaluating those bets
        rather than fabricate odds for them.
        """
        record = odds_lookup.get((home_team, away_team, match_date))
        if record is None:
            return None

        if market == "match_winner":
            return {
                "home_win": record.odds_home_win,
                "draw": record.odds_draw,
                "away_win": record.odds_away_win,
            }.get(selection)

        if market == "match_total_goals" and selection == "over_2.5":
            # This data source only provides the 2.5 goals line - the
            # other GOAL_LINES (0.5, 1.5, 3.5, 4.5) have no odds coverage
            # here and correctly return None below.
            return record.odds_over_2_5

        return None

    # odds_provider is real now (backed by actual historical odds from
    # xgabora/Club-Football-Match-Data - this dataset does not document
    # which specific bookmaker its OddHome/OddDraw/OddAway columns come
    # from, so these are treated as "a" bookmaker's real market odds,
    # not confirmed as Bet365 specifically), unlike the None passed
    # here previously - see the module docstring's now-partially-resolved
    # "UNRESOLVED GAP" section.
    report = run_backtest(all_matches, odds_provider=odds_provider)

    logger.info(f"Running corners calibration check on {len(all_corners_rows)} rows with corners data...")
    corners_calibration = {}
    if len(all_corners_rows) >= 60:  # need enough for a meaningful min_training_matches window
        try:
            corners_calibration = _run_corners_calibration_check(all_corners_rows)
        except Exception as e:
            logger.error(f"Corners calibration check failed: {e}")
    else:
        logger.warning(
            f"Only {len(all_corners_rows)} rows with corners data - skipping "
            f"calibration check (needs more data for a meaningful result)."
        )

    corners_section_lines = ["", "Corners model calibration (predicted vs actual, home team, no odds available):"]
    if corners_calibration:
        for key in sorted(corners_calibration.keys(), key=lambda k: float(k.split("_", 1)[1])):
            predicted, actual, n = corners_calibration[key]
            if n == 0:
                corners_section_lines.append(f"  {key}: no predictions made")
            else:
                corners_section_lines.append(
                    f"  {key}: predicted={predicted:.1%}  actual={actual:.1%}  n={n}"
                )
    else:
        corners_section_lines.append("  (skipped - insufficient corners data)")

    os.makedirs(config.HISTORICAL_DATA_DIR, exist_ok=True)
    report_path = os.path.join(os.path.dirname(config.HISTORICAL_DATA_DIR), "backtest_report.txt")
    report_text = (
        "EPL Backtest Report\n"
        f"Seasons: {args.start}-{args.end}\n"
        f"Total historical matches fetched: {len(all_matches)}\n"
        f"Data source: xgabora/Club-Football-Match-Data (GitHub-hosted CSV, 2000-present)\n"
        "\n"
        "*** ODDS COVERAGE NOTE ***\n"
        "Real historical odds are only available for: match_winner "
        "(home_win/draw/away_win) and match_total_goals at the 2.5 line "
        "specifically (Over/Under 2.5 is the only goals line this data "
        "source provides odds for). Double chance, team total goals, "
        "team shots on target, and match shots on target have NO real "
        "odds source connected and will show zero bets below - this is "
        "expected and correct, not a bug, until a source for those "
        "markets' odds is found. Corners has no odds source either, but "
        "IS backed by real historical results, so a separate calibration "
        "check (predicted probability vs actual frequency) is reported "
        "below instead of a betting ROI.\n"
        "\n" + report.summary() + "\n"
        + "\n".join(corners_section_lines)
    )
    with open(report_path, "w") as f:
        f.write(report_text)
    logger.info(f"Report written to {report_path}")
    print(report_text)


def _run_corners_calibration_check(matches_with_corners, min_training_matches=50):
    """Standalone calibration check for CornersModel, run separately from
    the main betting backtest above since football-data.co.uk provides
    no corners odds at all (so corners can't be evaluated via Kelly
    staking the way match_winner/match_total_goals can - there's simply
    nothing to bet against). This instead checks CALIBRATION: when the
    model predicts "70% chance of Over 4.5 corners," does that actually
    happen about 70% of the time across many such predictions? That's a
    meaningful, odds-independent signal of whether the model is any
    good, even with no betting market to evaluate ROI against.

    matches_with_corners: list of objects with home_team, away_team,
        match_date/date, home_corners, away_corners attributes (e.g.
        HistoricalMatchOdds records from football-data.co.uk).

    Returns a dict mapping each corners line actually seen across all
    matches (e.g. "over_4.5") to (predicted_avg_probability,
    actual_frequency, n_predictions) - a well-calibrated model should
    show predicted_avg_probability close to actual_frequency for each
    line.

    NOTE ON DYNAMIC LINES: corners.py's team_over_probs() now generates
    a DIFFERENT range of lines per match, based on that match's specific
    expected corners (see corners.py's generate_lines()) - there is no
    longer a single fixed CORNERS_LINES list shared across every match.
    This function therefore aggregates results keyed by whatever lines
    actually appeared for each match, rather than assuming a shared
    universe of lines up front. A consequence: a given line (e.g.
    "over_9.5") will accumulate far fewer data points than a line most
    teams' ranges include (e.g. "over_2.5"), since only matches where a
    team's expected corners were high enough to generate that line
    contribute to it. This is expected and reported via each line's own
    n count - callers should treat low-n lines' calibration numbers with
    more caution than high-n lines.
    """
    from engine.prediction.corners import CornersModel, build_corners_profiles_from_historical

    sorted_matches = sorted(matches_with_corners, key=lambda m: m.date)
    predictions_by_line = {}

    for i, match in enumerate(sorted_matches):
        if i < min_training_matches:
            continue

        training_data = sorted_matches[:i]  # no lookahead, same discipline as run_backtest()
        profiles = build_corners_profiles_from_historical(training_data)
        if not profiles:
            continue

        model = CornersModel()
        model.fit(profiles)

        try:
            probs = model.team_over_probs(match.home_team, match.away_team)
        except Exception:
            continue

        actual_home_total = match.home_corners
        if actual_home_total is None:
            continue

        # Iterate over whatever lines this specific match's home-side
        # prediction actually generated (dynamic, per generate_lines()),
        # rather than a fixed shared list - see docstring above.
        for key, predicted_prob in probs["home"].items():
            line = float(key.split("_", 1)[1])
            actual_outcome = 1.0 if actual_home_total > line else 0.0
            predictions_by_line.setdefault(key, {"predicted": [], "actual": []})
            predictions_by_line[key]["predicted"].append(predicted_prob)
            predictions_by_line[key]["actual"].append(actual_outcome)

    result = {}
    for key, data in predictions_by_line.items():
        n = len(data["predicted"])
        if n == 0:
            result[key] = (None, None, 0)
            continue
        avg_predicted = sum(data["predicted"]) / n
        avg_actual = sum(data["actual"]) / n
        result[key] = (avg_predicted, avg_actual, n)

    return result


if __name__ == "__main__":
    _cli_main()
