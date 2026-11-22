#!/usr/bin/env bash
# Removes the old streak engine (openfootball fixtures + xgabora history + fixed-window streaks).
# Staged with `git rm`; the workflow checks that nothing else broke before committing.
set -uo pipefail

echo "== removing old engine =="
for p in engine/main.py engine/streaks engine/output/generate_html.py engine/kickoff.py \
         data/predictions data/historical; do
  if [ -e "$p" ]; then git rm -r -q "$p" && echo "removed $p"; else echo "not present: $p"; fi
done

# the old xgabora data source, wherever it lives
grep -rliE "class Xgabora" engine --include=*.py 2>/dev/null | while read -r f; do
  git rm -q "$f" && echo "removed $f"; done

# tests that belonged to the removed code
grep -rliE "engine\.(main|streaks|kickoff|output\.generate_html)|generate_html|xgabora" tests --include=*.py 2>/dev/null \
  | grep -vE "test_trends|test_data_layer" | while read -r f; do
  git rm -q "$f" && echo "removed $f"; done

echo
echo "KEPT on purpose: engine/sources/openfootball_source.py (its team-name table is still used"
echo "by engine/data/teams.py until the name mapping is verified on a real run)."
exit 0
