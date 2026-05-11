#!/bin/bash
# Quick smoke test — verify all scripts load and run without errors.
# Run from the repo root: bash scripts/smoke_test.sh

set -e
cd "$(dirname "$0")/.."

echo "=== 1. Physics model self-test ==="
python3 scripts/physics_model.py
echo

echo "=== 2. Tyre pressure (default 90.1 kg, 40/60) ==="
python3 scripts/tyre_pressure.py
echo

echo "=== 3. Training load — current week plan ==="
python3 scripts/training_load.py --ctl 42 --atl 43 --plan 62,60,20,150,0 --labels Wed,Thu,Fri,Sat,Sun
echo

echo "=== 4. FIT analyser — sanity check (needs a FIT file argument) ==="
echo "    Skipped — requires: python3 scripts/analyse_fit.py rides/<name>.fit"
echo

echo "=== 5. GPX analyser — sanity check (needs a GPX file argument) ==="
echo "    Skipped — requires: python3 scripts/analyse_gpx.py routes/<name>.gpx"
echo

echo "=== ALL SCRIPTS OK ==="
