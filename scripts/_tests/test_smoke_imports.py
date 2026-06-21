"""Smoke test: every framework module imports without error.

Highest-leverage guard given the history of import-time side effects in
profile.py — if a module crashes on import (bad profile read, missing dep,
syntax error), this fails fast. A small denylist covers one-off scripts and
modules with optional deps not in the base env.
"""
import importlib
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]

# One-offs / optional-dep scripts that aren't part of the importable core.
# (The build_ride_brief_pdf one-off now lives under the gitignored notes/.)
DENYLIST: set[str] = set()

MODULES = sorted(
    p.stem
    for p in SCRIPTS_DIR.glob("*.py")
    if p.stem not in DENYLIST and not p.stem.startswith("_")
)


@pytest.mark.parametrize("modname", MODULES)
def test_module_imports(modname):
    importlib.import_module(modname)
