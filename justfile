set shell := ["bash", "-euo", "pipefail", "-c"]

default: ci

# EXACTLY what CI's tests workflow runs (never creates/modifies the `cycling` env)
ci: test

test:
    conda run -n cycling pytest scripts/_tests -q

act:
    act pull_request -W .github/workflows/tests.yml
