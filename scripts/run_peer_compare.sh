#!/usr/bin/env bash
# run_peer_compare.sh — one-liner wrapper for rider-vs-peer FIT comparison.
#
# Usage:
#   scripts/run_peer_compare.sh <peer_name> <peer_fit> [route_gpx]
#
# Behaviour:
#   - Looks up <peer_name> in USER_PROFILE.md `peer_<name>:` registry for
#     labels, FTP, weight, and FTP source (so flags don't need re-passing).
#   - Auto-discovers the rider's matching FIT in rides/fit/ — picks the most
#     recent file whose date (filename YYYY-MM-DD prefix) matches the peer's
#     FIT recording date, or the latest if no match.
#   - If [route_gpx] is omitted, tries to glob a single GPX in routes/ whose
#     name matches recent peer fit. Otherwise runs without canonical climb
#     spans (find_climbs is run on the rider's FIT instead).
#   - Output goes to rides/analyses/{YYYY-MM-DD}-{route_stem}-vs-{peer}.md
#
# Examples:
#   scripts/run_peer_compare.sh thomas ~/Downloads/thomas.fit
#   scripts/run_peer_compare.sh thomas ~/Downloads/thomas.fit \
#     "routes/2026-03-01_2805778602_Lost Lane #21 Hidden Hertfordshire.gpx"
#
# Requires conda env `cycling` (per CLAUDE.md global env-isolation rule).
set -euo pipefail

PEER_NAME="${1:-}"
PEER_FIT="${2:-}"
ROUTE_GPX="${3:-}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYBIN="/opt/miniconda3/envs/cycling/bin/python"

if [ -z "$PEER_NAME" ] || [ -z "$PEER_FIT" ]; then
  echo "Usage: $(basename "$0") <peer_name> <peer_fit> [route_gpx]" >&2
  echo "" >&2
  echo "Registered peers:" >&2
  # Resolve the scripts/ dir as an absolute path so this works regardless of CWD.
  # Quoted heredoc + argv so the repo path can't break/inject into the source.
  "$PYBIN" - "$REPO_ROOT" >&2 <<'PY' || echo "  (could not load registry)" >&2
import sys
sys.path.insert(0, sys.argv[1] + '/scripts')
from profile import list_peers
peers = list_peers()
print('  ' + (', '.join(peers) if peers else '(none registered)'))
PY
  exit 64
fi

if [ ! -f "$PEER_FIT" ]; then
  echo "ERROR: peer FIT not found: $PEER_FIT" >&2
  exit 2
fi

cd "$REPO_ROOT"

# 1. Auto-discover rider's matching FIT.
# Strategy: read peer FIT's session start date, find rider FIT with same date
# prefix (rides/fit/<YYYY-MM-DD>-*.fit), else fall back to most recent.
RIDER_FIT="$(
  "$PYBIN" - "$PEER_FIT" <<'PY'
import sys, glob
sys.path.insert(0, 'scripts')
from analyse_fit import parse_fit
sess, _, _ = parse_fit(sys.argv[1])   # path via argv, not source interpolation
start = sess.get("start_time") or sess.get("timestamp")
date_prefix = start.strftime("%Y-%m-%d") if start else None
candidates = sorted(glob.glob("rides/fit/*.fit"))
if date_prefix:
    matches = [c for c in candidates if date_prefix in c]
    if matches:
        print(matches[-1]); sys.exit(0)
print(candidates[-1] if candidates else "")
PY
)"

if [ -z "$RIDER_FIT" ] || [ ! -f "$RIDER_FIT" ]; then
  echo "ERROR: no rider FIT found in rides/fit/" >&2
  exit 3
fi
echo "Rider FIT (auto-discovered): $RIDER_FIT" >&2

# 2. Resolve output path.
RIDE_DATE="$(basename "$RIDER_FIT" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' || date +%Y-%m-%d)"
# Slug heuristic: take only the final underscore-separated segment of the GPX
# name (the human-readable route name; strips Komoot/Strava export ID prefix),
# slugify, then keep the first 3 hyphen-tokens for compactness.
# `2026-03-01_2805778602_Lost Lane #21 Hidden Hertfordshire.gpx`
#   → "Lost Lane #21 Hidden Hertfordshire" → "lost-lane-21"
if [ -n "$ROUTE_GPX" ]; then
  RAW_STEM="$(basename "$ROUTE_GPX" .gpx | awk -F'_' '{print $NF}')"
  SLUG="$(echo "$RAW_STEM" \
    | tr 'A-Z' 'a-z' \
    | tr ' ' '-' \
    | tr -cd 'a-z0-9-' \
    | tr -s '-' \
    | awk -F'-' '{print $1"-"$2"-"$3}' \
    | sed 's/-$//')"
  ROUTE_STEM="${SLUG:-ride}"
else
  ROUTE_STEM="ride"
fi
OUT_PATH="rides/analyses/${RIDE_DATE}-${ROUTE_STEM}-vs-${PEER_NAME}.md"
# Refuse to overwrite a file that has been hand-edited with synthesis sections.
# Heuristic: existing file > 6 KB AND contains a `## Synthesis` heading.
if [ -f "$OUT_PATH" ] \
   && [ "$(wc -c < "$OUT_PATH")" -gt 6000 ] \
   && grep -q '^## Synthesis' "$OUT_PATH"; then
  TS="$(date +%H%M%S)"
  OUT_PATH="rides/analyses/${RIDE_DATE}-${ROUTE_STEM}-vs-${PEER_NAME}.auto-${TS}.md"
  echo "Existing canonical analysis detected; writing fresh auto-content to a" >&2
  echo "separate file so your hand-added sections aren't overwritten:" >&2
  echo "  $OUT_PATH" >&2
fi
mkdir -p "$(dirname "$OUT_PATH")"

# 3. Build flags. The --peer flag pulls all the common config from the registry.
GPX_FLAG=""
if [ -n "$ROUTE_GPX" ]; then
  if [ ! -f "$ROUTE_GPX" ]; then
    echo "WARNING: route GPX not found, skipping canonical climb spans: $ROUTE_GPX" >&2
  else
    GPX_FLAG="--gpx $(printf '%q' "$ROUTE_GPX")"
  fi
fi

echo "Output: $OUT_PATH" >&2
echo "" >&2

# 4. Invoke. Use eval to expand the quoted GPX path correctly.
eval "$PYBIN scripts/compare_riders.py \
  $(printf '%q' "$RIDER_FIT") \
  $(printf '%q' "$PEER_FIT") \
  --peer $(printf '%q' "$PEER_NAME") \
  $GPX_FLAG \
  --out $(printf '%q' "$OUT_PATH")"

echo "" >&2
echo "Wrote: $OUT_PATH" >&2
