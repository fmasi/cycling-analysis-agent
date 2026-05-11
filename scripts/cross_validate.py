"""Cross-validate the climb verifier against a FIT-recorded ride.

For each ride in a list of FIT files:
1. Convert FIT -> trace GPX (FIT GPS + altitude, 1 Hz)
2. Find "FIT-truth climbs" by running find_climbs on the FIT altitudes
3. Run the full verifier on the trace GPX (DEM-based; needs tiles in the area)
4. Diff the two climb lists by km-range overlap

Output: per-ride table of FIT-truth vs verifier (declared + missed),
flagging any FIT climb the verifier didn't find. Also reports the peak
deltas so the rider can see whether the hi-fi reading agrees with their
FIT recording.

Usage:
    python scripts/cross_validate.py <fit1> [<fit2> ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyse_fit import parse_fit, to_arrays
from analyse_gpx import find_climbs
from fit_to_gpx import fit_to_gpx
from local_dem import LocalDEM
from verify_climbs import verify_route


def _overlaps(a_lo, a_hi, b_lo, b_hi):
    return not (a_hi < b_lo or b_hi < a_lo)


def _match(fit_climb, verifier_climbs):
    """Return the verifier climb whose km-range overlaps the FIT climb, or None."""
    for cv in verifier_climbs:
        if _overlaps(
            fit_climb["start_km"], fit_climb["end_km"],
            cv.km_start, cv.km_end,
        ):
            return cv
    return None


def cross_validate(fit_path: Path, dem: LocalDEM, *, verbose: bool = True) -> dict:
    """Returns {fit_truth: [...], verifier_declared: [...], verifier_missed: [...],
    coverage_gaps: [...], peak_deltas: [...]}."""
    sess, recs, _ = parse_fit(str(fit_path))
    a = to_arrays(recs)
    if a is None:
        return {"error": "no records"}

    # 1) FIT-truth climbs: find_climbs on the FIT's own altitudes.
    fit_climbs = find_climbs(a["distance_m"], a["altitude_m"])

    # 2) Convert + run verifier on trace GPX.
    gpx_path = Path("routes") / f"{fit_path.stem}-trace.gpx"
    fit_to_gpx(fit_path, gpx_path)
    report = verify_route(gpx_path, dem, fallback=None)

    declared = report.climbs
    missed = report.missed_climbs
    all_verifier = list(declared) + list(missed)

    # 3) Diff by km-range overlap. Any FIT-truth climb without a match is a
    # coverage gap (something the verifier didn't find that the rider's
    # bike computer did).
    coverage_gaps = []
    peak_deltas = []
    for fc in fit_climbs:
        match = _match(fc, all_verifier)
        if match is None:
            coverage_gaps.append(fc)
        else:
            peak_deltas.append({
                "km": fc["start_km"],
                "fit_peak": fc["max_grad_pct"],
                "hifi_peak": match.verified_peak_pct,
                "delta_pp": match.verified_peak_pct - fc["max_grad_pct"],
                "via": "declared" if match in declared else "missed",
            })

    # 4) Also note any verifier climbs that DON'T match a FIT-truth climb
    # — these are potential false positives.
    extras = []
    for cv in all_verifier:
        fc_like = {"start_km": cv.km_start, "end_km": cv.km_end}
        matched = False
        for fc in fit_climbs:
            if _overlaps(
                fc["start_km"], fc["end_km"],
                cv.km_start, cv.km_end,
            ):
                matched = True
                break
        if not matched:
            extras.append({"km": cv.km_start, "peak": cv.verified_peak_pct})

    if verbose:
        print(f"\n=== {fit_path.name} ===")
        print(f"  FIT-truth climbs: {len(fit_climbs)}")
        print(f"  Verifier declared: {len(declared)}")
        print(f"  Verifier missed (Layer 2): {len(missed)}")
        print(f"  Coverage gaps: {len(coverage_gaps)}")
        print(f"  Extras (verifier-only): {len(extras)}")
        if peak_deltas:
            print("\n  Peak agreement (FIT vs hi-fi):")
            print("    km    FIT peak   Hi-fi peak    Δ        via")
            for d in peak_deltas:
                print(f"    {d['km']:>5.2f}   {d['fit_peak']:>5.1f}%      "
                      f"{d['hifi_peak']:>5.1f}%      "
                      f"{'+' if d['delta_pp'] >= 0 else ''}{d['delta_pp']:>4.1f}pp   {d['via']}")
        if coverage_gaps:
            print("\n  ⚠ FIT climbs the verifier did NOT find:")
            for g in coverage_gaps:
                print(f"    km {g['start_km']:>5.2f}-{g['end_km']:.2f}  "
                      f"len {g['length_m']:.0f}m  gain {g['gain_m']:.0f}m  "
                      f"max {g['max_grad_pct']:.1f}%")
        if extras:
            print("\n  Verifier-only climbs (not in FIT's find_climbs):")
            for e in extras:
                print(f"    km {e['km']:>5.2f}  peak {e['peak']:.1f}%")

    return {
        "fit_truth": fit_climbs,
        "verifier_declared": declared,
        "verifier_missed": missed,
        "coverage_gaps": coverage_gaps,
        "peak_deltas": peak_deltas,
        "extras": extras,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fits", type=Path, nargs="+")
    p.add_argument("--dem-root", type=Path,
                   default=Path.home() / "cycling-coach-dem")
    args = p.parse_args(argv)

    dem = LocalDEM(args.dem_root)
    for f in args.fits:
        cross_validate(f, dem)


if __name__ == "__main__":
    main()
