# Review rubric: cycling-analysis-agent

This file is read by BOTH the CI Claude review (fmasi/.github `claude-review.yml`) and the local
`/ci-review` command, so a branch that passes `/ci-review` should pass CI's review too.

Review the change, not the whole codebase. Read the surrounding code when a hunk depends on it.
Report only material problems: no praise, no summary of what the code does, no style nits that
ruff already catches.

## What this repo protects

The scripts produce the numbers a rider and coach act on: training load (CTL / ATL / TSB and
weekly TSS), FIT analysis (power, NP, IF, TSS, zones, heart rate, power curve), climb detection,
categories and LIDAR verification, and speed / power / time predictions. A silent formula or unit
regression gives confident, wrong advice about fatigue, form and pacing. The repo is public, so
anything committed is published: the rider's data (profile, rides, routes, body composition) must
never enter it.

The spec is CLAUDE.md ("Training load definitions", "Power zone formulas", "HR zone formula",
"Physics model", "Things to never do"). Among others:
- CTL / ATL: EMAs with `alpha = 1 - exp(-1/42)` and `1 - exp(-1/7)`;
- TSB: lag-1 (yesterday's CTL − yesterday's ATL), never same-day except in clearly labelled
  end-of-day forecasts;
- TSS from timer time, never elapsed; the stored TSS is read before recomputing;
- W/kg uses body weight, physics uses system weight (body + bike + kit): never mixed;
- predictions carry ranges, not single points.

## What to check, in priority order

1. **Numerical correctness.** Formulas against the CLAUDE.md conventions above; units (W vs W/kg,
   m vs km, s vs min, % vs fraction, km/h vs m/s); rolling windows (NP's 30 s), EMA constants,
   off-by-one in day or sample indexing; the lag-1 TSB; division by zero or empty series (a ride with
   no power, a zero-length climb); NaN from missing FIT fields propagating into a report.
2. **Personal data and secrets.** A real rider profile, FIT/GPX/TCX file, ride analysis, body
   composition or route in a tracked file, test or fixture (tests use the synthetic profile in
   `scripts/_tests/conftest.py`); `.gitignore` loosened for the personal folders; the GPXZ key or any
   other token in code, tests, logs or docs.
3. **Security.** Archive extraction (py7zr / zip for DEM downloads) without path-traversal checks;
   network calls without timeouts; untrusted file content used in shell commands or paths.
4. **Tests.** New or changed metric code has known-answer tests with synthetic inputs, and the
   expected values are worked out from the formula, not recomputed with the code under test
   (`scripts/_tests/test_training_load.py` is the pattern). Tests stay hermetic: no network, no real
   DEM tiles, never the real `USER_PROFILE.md`. A bug fix comes with a test that fails without it.
   The coverage floor (`--cov-fail-under` in the justfile) never goes down. Name the untested
   behaviour.
5. **Consistency and docs.** Follows CONTRIBUTING.md and CLAUDE.md. When a convention or formula changes,
   CLAUDE.md, README and the script docstrings change with it, and the PR description says it's a
   coaching decision. New dependencies go in `environment.yml` with loose `>=` floors that resolve on
   osx-arm64 and linux-64. An import that looks unused may be a deliberate re-export
   (`physics_model`): check who imports it from there before removing it.
6. **CI hygiene** (only when workflows, the justfile or lefthook.yml change). Draft skip,
   concurrency, `timeout-minutes`, no `push` on all branches, no `paths-ignore` on
   `tests.yml` (its `pytest` job is a required check), and `just ci` still mirrors CI.

## What this review can't see

Say so when a change depends on one of these, rather than assuming it's fine:
- **Largely untested code.** `analyse_fit.py` (power, HR, zones), `tyre_pressure.py`,
  `analyse_climbs.py` (except its report), the `chart_*` scripts, `compare_riders.py`,
  `cross_validate.py` and `fit_to_gpx.py` have little or no behavioural coverage (overall about
  46 %). A change there is checked only by this review and the import smoke test.
- **Real rides and the real profile.** They are gitignored, so no test runs on a real FIT file or on
  the rider's actual numbers. Charts are never checked visually.
- **The environment.** There is no lockfile: CI resolves `environment.yml`'s `>=` floors fresh each
  run, so CI and the laptop can run different library versions, and GitHub's dependency graph
  can't see conda packages (Dependabot is blind here; pip-audit in `just ci` is the scan).
- **External services** (OSRM map-matching, GPXZ, IGN / DEFRA DEM downloads) are mocked in tests.

## How to rank

- **Critical**: breaks production or the build, loses or corrupts data, leaks a secret or
  personal data, or opens a security hole. In this repo that includes a metric that is now wrong
  for a real ride (a changed EMA constant, same-day TSB presented as current form, elapsed time in
  TSS, body and system weight swapped) and any rider data in a tracked file. Must fix before merge.
  One Critical finding makes the CI review end with `REVIEW-VERDICT: BLOCK` (label
  `claude-blocked`), which blocks the merge until a re-review passes. Don't inflate: a Critical is
  something the owner would roll back for.
- **Important**: a real bug on a path that will happen, missing error handling, changed
  behaviour without a test, or docs that are now wrong. Should fix before merge.
- **Minor**: worth fixing, safe to merge without.

## Out of scope

- `docs/superpowers/**` (historical plans and specs) and `docs/index.html` / `docs/assets/**` (the
  static project page): check them only for personal data.

## Output

One line per finding, most severe first:
`[Critical|Important|Minor] path/to/file:line: what is wrong, why it matters, the fix.`
If nothing material turns up, say so in one line.
