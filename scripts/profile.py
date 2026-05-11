"""
Rider profile loader.

Reads the YAML frontmatter of `USER_PROFILE.md` at the repo root (or, if it
doesn't exist, `USER_PROFILE.example.md`). Any field the loader cares about
but the profile doesn't define falls back to a generic adult-cyclist default,
so a stranger cloning the repo can run all scripts without writing a profile
first.

Defaults are deliberately neutral:
    FTP             200 W
    MAP (working)   250 W
    AC (1-min)      350 W
    NM (5-15s)      600 W
    Rider weight    75.0 kg     (for W/kg)
    Bike weight     9.0 kg
    Kit weight      3.0 kg      (shoes, helmet, bottles, etc.)
    System weight   87.0 kg     (rider + bike + kit)
    F/R split       48 / 52     (Silca default — used when no measurement exists)
    CdA             0.30
    CRR             0.0055
    Drivetrain eff  0.97
    Air density     1.225 kg/m^3
    Gravity         9.81 m/s^2
    Wheel circ.     2.155 m     (700c x 32mm)
    Max HR          190 bpm
    Rest HR         55 bpm
    LTHR            165 bpm

Usage:
    from profile import FTP, MAP_WORKING, RIDER_WEIGHT_KG, SYSTEM_WEIGHT_KG
    # or, when you need everything:
    from profile import load_profile
    p = load_profile()
    print(p["fitness"]["ftp_w"])

The module-level constants (FTP, MAP_WORKING, etc.) are populated at import
time from `load_profile()`. They are the convenient ergonomic API. The
underlying dict is available for code that wants to introspect the full
profile.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Generic defaults — used when a profile is missing or doesn't define a field.
# These match the published examples and the framework's documented defaults.
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, dict[str, Any]] = {
    "identity": {
        "name": "Generic rider",
        "location": "",
    },
    "body": {
        "weight_kg": 75.0,
    },
    "fitness": {
        "ftp_w": 200,
        "map_w_working": 250,
        "map_w_test": 250,
        "ac_w": 350,
        "nm_w": 600,
        "lthr_bpm": 165,
        "max_hr_bpm": 190,
        "rest_hr_bpm": 55,
    },
    "physics": {
        "bike_weight_kg": 9.0,
        "kit_weight_kg": 3.0,
        "system_weight_kg": 87.0,            # rider + bike + kit
        "cda": 0.30,
        "fr_split_front_pct": 48,            # Silca's 48/52 reference
        "drivetrain_efficiency": 0.97,
        "crr": 0.0055,
        "air_density_kg_m3": 1.225,
        "gravity_m_s2": 9.81,
        "wheel_circ_m": 2.155,
    },
    "training_load": {
        "ctl": 0.0,
        "atl": 0.0,
        "tsb": 0.0,
    },
}


# ---------------------------------------------------------------------------
# YAML frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL
)


def _coerce_scalar(raw: str) -> Any:
    """Best-effort coercion for a YAML scalar without a yaml dependency."""
    s = raw.strip()
    if s == "" or s.startswith("<"):
        # Placeholder like `<e.g. 200>` — treat as missing
        return None
    # Strip optional inline comment
    if "#" in s:
        # Don't strip if inside quotes
        if not (s.startswith('"') or s.startswith("'")):
            s = s.split("#", 1)[0].strip()
    # Strip quotes
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        s = s[1:-1]
    # Try int, then float, else string
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a 2-level indented YAML doc (block → key: value).

    We intentionally don't depend on PyYAML — the frontmatter shape is fixed
    and shallow, and avoiding the dep keeps the loader trivial to ship.

    Recognises:
        section:
          key: value
          key: value

    Lines that don't fit this pattern (free text outside the frontmatter
    block, '#' comments, blank lines) are skipped.
    """
    out: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Top-level: "section:"
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            out.setdefault(current_section, {})
            continue
        # Indented key: value
        if line.startswith(" ") and ":" in line and current_section:
            key, _, val = line.strip().partition(":")
            coerced = _coerce_scalar(val)
            if coerced is not None:
                out[current_section][key.strip()] = coerced
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Shallow-section, shallow-key merge — override wins per key."""
    merged: dict[str, Any] = {k: dict(v) for k, v in base.items()}
    for section, kv in override.items():
        if not isinstance(kv, dict):
            continue
        merged.setdefault(section, {})
        for key, val in kv.items():
            merged[section][key] = val
    return merged


# ---------------------------------------------------------------------------
# Profile discovery + loading
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """The directory above scripts/ — works whether the loader is imported
    from scripts/ or run as `python scripts/profile.py`."""
    return Path(__file__).resolve().parent.parent


def _find_profile_path() -> Path | None:
    root = _repo_root()
    real = root / "USER_PROFILE.md"
    if real.exists():
        return real
    example = root / "USER_PROFILE.example.md"
    if example.exists():
        return example
    return None


def load_profile() -> dict[str, Any]:
    """Return the merged profile dict (defaults + frontmatter overrides).

    Always returns a complete shape — every section in DEFAULTS is present
    and every documented key has a value.
    """
    path = _find_profile_path()
    if path is None:
        return _deep_merge(DEFAULTS, {})
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return _deep_merge(DEFAULTS, {})
    parsed = _parse_simple_yaml(match.group("body"))
    return _deep_merge(DEFAULTS, parsed)


# ---------------------------------------------------------------------------
# Ergonomic module-level constants
# ---------------------------------------------------------------------------

_p = load_profile()

# Fitness
FTP: int = int(_p["fitness"]["ftp_w"])
MAP_WORKING: int = int(_p["fitness"]["map_w_working"])
MAP_TEST: int = int(_p["fitness"].get("map_w_test", MAP_WORKING))
AC_FRESH_EST: int = int(_p["fitness"]["ac_w"])
NM_PEAK: int = int(_p["fitness"]["nm_w"])
LTHR_BPM: int = int(_p["fitness"]["lthr_bpm"])
MAX_HR_BPM: int = int(_p["fitness"]["max_hr_bpm"])
REST_HR_BPM: int = int(_p["fitness"].get("rest_hr_bpm", DEFAULTS["fitness"]["rest_hr_bpm"]))

# Body / physics
RIDER_WEIGHT_KG: float = float(_p["body"]["weight_kg"])
BIKE_WEIGHT_KG: float = float(_p["physics"]["bike_weight_kg"])
KIT_WEIGHT_KG: float = float(_p["physics"].get("kit_weight_kg", DEFAULTS["physics"]["kit_weight_kg"]))
SYSTEM_WEIGHT_KG: float = float(_p["physics"]["system_weight_kg"])
CDA_DEFAULT: float = float(_p["physics"]["cda"])
CRR_DEFAULT: float = float(_p["physics"]["crr"])
DRIVETRAIN_EFFICIENCY: float = float(_p["physics"]["drivetrain_efficiency"])
AIR_DENSITY: float = float(_p["physics"]["air_density_kg_m3"])
GRAVITY: float = float(_p["physics"]["gravity_m_s2"])
WHEEL_CIRCUMFERENCE_M: float = float(_p["physics"]["wheel_circ_m"])
FR_SPLIT_FRONT_PCT: float = float(_p["physics"]["fr_split_front_pct"])


def power_zone_bounds() -> list[tuple[str, int, int]]:
    """Materialise the eight power zones for this profile's FTP/MAP/AC/NM.

    Returns a list of (zone_name, watts_lower, watts_upper) inclusive bounds,
    derived from the formulas in CLAUDE.md. Adjacent zones touch.
    """
    ftp = FTP
    return [
        ("Z1 Recovery",     0,                   round(ftp * 0.55) - 1),
        ("Z2 Endurance",    round(ftp * 0.55),   round(ftp * 0.70)),
        ("Z3 Tempo",        round(ftp * 0.71),   round(ftp * 0.90)),
        ("Z4 Sweet Spot",   round(ftp * 0.85),   round(ftp * 0.95)),
        ("Z5 Threshold",    round(ftp * 0.92),   ftp),
        ("Z6 MAP",          ftp + 1,             MAP_WORKING),
        ("Z7 AC",           MAP_WORKING + 1,     AC_FRESH_EST),
        ("Z8 NM",           AC_FRESH_EST + 1,    NM_PEAK),
    ]


if __name__ == "__main__":
    import json
    print("Loaded profile (with defaults filled in):")
    print(json.dumps(_p, indent=2, default=str))
    print("\nMaterialised power zones (W):")
    for name, lo, hi in power_zone_bounds():
        print(f"  {name:<18} {lo:>4} – {hi:<4}")
