# cycling-analysis-agent: how changes land (for people and coding agents)

Claude Code reads this file through `@CONTRIBUTING.md` in CLAUDE.md; point any other coding agent
at it. The owner doesn't read code. The checks below and the one Claude review on GitHub are the
safety net, so follow them exactly.

**Why not AGENTS.md** (the usual home for these rules): when this repo runs as an OpenClaw
workspace, the host puts its own `AGENTS.md` (with `SOUL.md`, `IDENTITY.md`, ...) at the workspace
root, which is this repo's root, so `/AGENTS.md` is gitignored. A tracked one would collide with
it on the next `git pull` there.

**Scope.** This file is for agents that CHANGE this repo. If you are coaching (analysing a ride,
planning training), your manual is the rest of CLAUDE.md, and nothing here changes that.

The scripts compute the numbers coaching decisions rest on: training load (CTL/ATL/TSB), FIT power
and heart-rate analysis, climb categories and verification, speed and power predictions. A wrong
formula gives confident, wrong advice, so it must never slip in silently. The repo is public, and
the rider's personal data must never enter it.

## How work lands here

1. Branch from `main`. Never commit on `main`.
2. Commit in small steps. The pre-commit hook (lefthook) runs ruff, gitleaks and shellcheck.
   Once per clone: `lefthook install`.
3. `just ci` must pass before every push. The pre-push hook runs it.
4. Push the branch and open a draft PR: `gh pr create --draft --fill`. CI does nothing on drafts.
5. Review locally before asking for the GitHub review. In Claude Code: `/ci-review`. Fix what it finds.
6. Push, WAIT until the push has landed, then run `gh pr ready`. That runs CI once and the one
   Claude review. Marking ready in the same second as a push can review the previous commit.
7. The merge needs `pytest` and `review / review-gate` green. The gate is green while the PR
   has the label `claude-reviewed` and not `claude-blocked`. Only the review workflow (or the owner)
   sets those labels.
8. `claude-blocked` means the review found a Critical issue. Fix it, push, then ask for a re-review
   by removing and re-adding `ready-for-review`:
   `gh pr edit <N> --remove-label ready-for-review && gh pr edit <N> --add-label ready-for-review`.
9. The rules bind everyone, the owner included. Nobody bypasses them.

## Commands

`just --list` shows every recipe. They run in the existing `cycling` conda env and never create or
modify it. The dev tools (`pytest-cov`, `ruff`, `pip-audit`) are in `environment.yml`, so update the env
from it once (`conda env update -n cycling -f environment.yml`).

- `just ci`: exactly what CI runs (lint, security, workflows, test, audit).
- `just lint`: ruff, with the rule set pinned in `pyproject.toml`.
- `just security`: ruff's bandit rules (SAST).
- `just workflows`: actionlint + zizmor on `.github/workflows` (`brew install actionlint zizmor`).
- `just test`: the hermetic pytest suite (`scripts/_tests/`) with the coverage floor.
- `just audit`: pip-audit over the env's Python packages.
- `just secrets`: gitleaks on the staged changes (the pre-commit hook runs it).

## Repo rules

- **Numbers are tested with known answers.** New or changed metric code (training load, power,
  TSS/IF/NP, zones, physics, climb categories) comes with tests in `scripts/_tests/` whose expected
  values are worked out by hand from the formula, with synthetic inputs, not recomputed with the
  code under test (see `test_training_load.py`). A bug fix comes with a test that fails without it.
- **The conventions in CLAUDE.md are the spec.** For example: TSB is lag-1 (yesterday's CTL − ATL),
  CTL/ATL use `1 − exp(−1/42)` and `1 − exp(−1/7)`, TSS uses timer time, W/kg uses body weight and
  physics uses system weight. Changing one is a coaching decision: say so in the PR description.
- **Tests are hermetic.** No network (mock map-matching, DEM downloads and the elevation fallback),
  no real DEM tiles, never the real `USER_PROFILE.md`: use the synthetic profile in
  `scripts/_tests/conftest.py`. Unit tests live in `scripts/_tests/`; the top-level `tests/` is
  the rider's personal fitness-test notes and is gitignored.
- **Personal data never enters git.** `USER_PROFILE.md`, `rides/`, `routes/`, `tests/`, `notes/`,
  `plans/`, `body-comp/`, and any `*.fit` / `*.gpx` / `*.tcx` stay gitignored: don't loosen
  `.gitignore`, never `git add -f`. The GPXZ key lives outside the repo (`~/.config/cycling-coach/`).
- **Dependencies** go in `environment.yml` as top-level packages with loose `>=` floors that resolve
  on osx-arm64 and linux-64 (CLAUDE.md, "Conda environment portability").
- **Re-exports are deliberate.** Some modules re-export names for their importers (for example
  `physics_model`, with a `# noqa: F401` on each). Don't remove an "unused" import without checking
  who imports it from there.
- **The coverage floor only goes up.** Raise `--cov-fail-under` in the justfile when coverage rises;
  never lower it to make a change pass.
- Follow the conventions in the existing code and in `.github/claude-review-prompt.md`, the rubric
  the Claude review applies.

## Never

- Never merge with `gh pr merge --admin`, and never try any other way around the ruleset.
- Never use `--no-verify` (on `git commit` or `git push`) to skip the hooks or `just ci`.
- Never add the `claude-reviewed` label yourself, and never remove `claude-blocked`. Only the review
  workflow and the owner do that.
- Never push to `main`. Every change goes through a PR.
- Never commit secrets, tokens, `.env` files, personal data (rides, profile, body composition) or
  notebook outputs.
