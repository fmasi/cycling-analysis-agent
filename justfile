set shell := ["bash", "-euo", "pipefail", "-c"]

# Runs a tool inside the `cycling` conda env. Locally that's your existing env: these
# recipes never create or modify it (see "Conda environment portability" in CLAUDE.md
# to update it from environment.yml). CI activates its own env from environment.yml
# first and sets CONDA_RUN to "" so the tools run straight from PATH.
conda_run := env("CONDA_RUN", "conda run -n cycling")

default: ci

# EXACTLY what CI runs (tests.yml runs gitleaks, then `just ci` in the env)
ci: lint security workflows test audit

# ruff lint (rule set pinned in pyproject.toml)
lint:
    {{ conda_run }} ruff check .

# SAST: ruff's bandit rules (S); asserts (S101) are fine in tests
security:
    {{ conda_run }} ruff check --select S --ignore S101 .

# workflow lint, the same command as just-ci.yml's workflow-lint (brew install actionlint zizmor)
workflows:
    @for t in actionlint zizmor; do command -v "$t" >/dev/null || { echo "$t missing: brew install $t (CI: tests.yml installs both)" >&2; exit 1; }; done
    actionlint
    zizmor --min-severity high .github/workflows

# hermetic test suite with the coverage floor (46.3% on 2026-09-28; raise it over time, never lower it)
test:
    {{ conda_run }} pytest scripts/_tests -q --cov --cov-report=term --cov-fail-under=45

# known-vulnerable Python packages in the env (pip-audit)
audit:
    {{ conda_run }} pip-audit

# scan the staged changes for secrets (the pre-commit hook runs this)
secrets:
    gitleaks git --staged --redact --no-banner

# workflow parity, occasionally
act:
    act pull_request -W .github/workflows/tests.yml
