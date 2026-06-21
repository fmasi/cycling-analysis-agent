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

Schema
------
The profile carries per-bike physics under a nested `bikes:` registry and
names the active bike with a top-level `default_bike:` scalar::

    default_bike: tripster
    bikes:
      tripster:
        system_weight_kg_default: 92.1
        fr_split: "40/60"
        ...

`load_profile()` folds the active bike's physics into the `physics` section so
the module-level constants below reflect the active bike. Older single-bike
profiles that carry a flat top-level `physics:` block keep working unchanged.
For the full typed bike API (per-bike CdA, gearing, assist, surfaces) use
`bike_config.load_bike()`.

Usage:
    from profile import FTP, MAP_WORKING, RIDER_WEIGHT_KG, SYSTEM_WEIGHT_KG
    # or, when you need everything:
    from profile import load_profile
    p = load_profile()
    print(p["fitness"]["ftp_w"])

The module-level constants (FTP, MAP_WORKING, ...) are computed lazily on first
access (PEP 562 `__getattr__`), so a bare `import profile` has no side effects
and never touches the filesystem until a constant or `load_profile()` is
actually used. This keeps the module cheap to import and trivially testable.
"""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path
from typing import Any

import yaml


def _warn(message: str) -> None:
    """Emit a non-fatal warning to stderr (keeps stdout clean for callers)."""
    print(f"[profile] warning: {message}", file=sys.stderr)


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
# YAML frontmatter parsing (PyYAML)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL
)


def _extract_frontmatter(text: str) -> dict[str, Any]:
    """Parse the leading `--- ... ---` YAML frontmatter block into a dict.

    Tolerates a leading HTML comment block (the template carries one) before
    the opening `---`.
    """
    stripped = text.lstrip()
    if stripped.startswith("<!--"):
        end = stripped.find("-->")
        if end != -1:
            stripped = stripped[end + 3:].lstrip()
    match = _FRONTMATTER_RE.match(stripped)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group("body"))
    except yaml.YAMLError as exc:
        _warn(f"could not parse profile frontmatter ({exc}); using defaults")
        return {}
    return data if isinstance(data, dict) else {}


def parse_fr_split(raw: Any) -> float | None:
    """Parse a front/rear weight split into the front-share percent.

    Accepts the canonical `"40/60"` string (-> 40.0) or a bare number.
    Returns None when it can't be parsed.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*\d+(?:\.\d+)?\s*$", s)
    if m:
        return float(m.group(1))
    try:
        return float(s)
    except ValueError:
        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge `override` onto a copy of `base`.

    Sections (dict values) merge per-key; scalar top-level keys (e.g.
    `default_bike`) and whole nested blocks (e.g. `bikes`) are taken from
    `override` as-is. `override` wins on every key it defines.
    """
    merged: dict[str, Any] = {k: dict(v) for k, v in base.items()}
    for key, val in override.items():
        if isinstance(val, dict):
            existing = merged.get(key)
            if isinstance(existing, dict):
                existing.update(val)
            else:
                merged[key] = dict(val)
        else:
            merged[key] = val
    return merged


# bike key -> physics key it populates (simple scalar fields on the bike)
_BIKE_TO_PHYSICS = {
    "system_weight_kg_default": "system_weight_kg",
    "bike_weight_kg": "bike_weight_kg",
    "cda": "cda",
    "drivetrain_efficiency": "drivetrain_efficiency",
    "wheel_circ_m": "wheel_circ_m",
}


def _resolve_default_physics(profile: dict[str, Any]) -> dict[str, Any]:
    """Fold the active bike's physics into the `physics` section.

    If the profile names a `default_bike` present in `bikes`, override the
    physics constants with that bike's values. Bad/non-numeric fields warn and
    keep the default rather than crashing the import of every script. Profiles
    without a `bikes:` block (flat `physics:` only) are returned unchanged.
    """
    default_bike = profile.get("default_bike")
    bikes = profile.get("bikes") or {}
    if not default_bike:
        return profile
    if default_bike not in bikes:
        _warn(
            f"default_bike={default_bike!r} not found in bikes: "
            f"{sorted(bikes) or 'none'}; using physics defaults"
        )
        return profile

    bike = bikes[default_bike] or {}
    phys = dict(profile.get("physics") or DEFAULTS["physics"])

    for src, dst in _BIKE_TO_PHYSICS.items():
        if bike.get(src) is not None:
            try:
                phys[dst] = float(bike[src])
            except (TypeError, ValueError):
                _warn(
                    f"bike '{default_bike}' {src}={bike[src]!r} is not numeric; "
                    f"keeping default {phys.get(dst)}"
                )

    front = parse_fr_split(bike.get("fr_split"))
    if front is not None:
        phys["fr_split_front_pct"] = front
    elif bike.get("fr_split") is not None:
        _warn(
            f"bike '{default_bike}' fr_split={bike['fr_split']!r} unparseable; "
            f"keeping default {phys.get('fr_split_front_pct')}% front"
        )

    out = dict(profile)
    out["physics"] = phys
    return out


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


@functools.lru_cache(maxsize=None)
def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    """Return the merged profile dict (defaults + frontmatter + active bike).

    Always returns a complete shape — every section in DEFAULTS is present and
    every documented key has a value. Pass `path` to load a specific profile
    file (used by tests); omit it to auto-discover USER_PROFILE.md /
    USER_PROFILE.example.md. Results are cached per path.
    """
    resolved = Path(path) if path is not None else _find_profile_path()
    if resolved is None:
        return _resolve_default_physics(_deep_merge(DEFAULTS, {}))
    text = resolved.read_text(encoding="utf-8")
    parsed = _extract_frontmatter(text)
    merged = _deep_merge(DEFAULTS, parsed)
    return _resolve_default_physics(merged)


# ---------------------------------------------------------------------------
# Lazy module-level constants (PEP 562)
#
# Computed on first attribute access so a bare `import profile` is
# side-effect-free. `from profile import FTP` triggers the load at the
# consumer's import time, as expected.
# ---------------------------------------------------------------------------

_CONSTANTS_CACHE: dict[str, Any] = {}


def _constants() -> dict[str, Any]:
    if not _CONSTANTS_CACHE:
        p = load_profile()
        f, b, phys = p["fitness"], p["body"], p["physics"]
        _CONSTANTS_CACHE.update(
            # Fitness
            FTP=int(f["ftp_w"]),
            MAP_WORKING=int(f["map_w_working"]),
            MAP_TEST=int(f.get("map_w_test", f["map_w_working"])),
            AC_FRESH_EST=int(f["ac_w"]),
            NM_PEAK=int(f["nm_w"]),
            LTHR_BPM=int(f["lthr_bpm"]),
            MAX_HR_BPM=int(f["max_hr_bpm"]),
            REST_HR_BPM=int(f.get("rest_hr_bpm", DEFAULTS["fitness"]["rest_hr_bpm"])),
            # Body / physics
            RIDER_WEIGHT_KG=float(b["weight_kg"]),
            BIKE_WEIGHT_KG=float(phys["bike_weight_kg"]),
            KIT_WEIGHT_KG=float(phys.get("kit_weight_kg", DEFAULTS["physics"]["kit_weight_kg"])),
            SYSTEM_WEIGHT_KG=float(phys["system_weight_kg"]),
            CDA_DEFAULT=float(phys["cda"]),
            CRR_DEFAULT=float(phys["crr"]),
            DRIVETRAIN_EFFICIENCY=float(phys["drivetrain_efficiency"]),
            AIR_DENSITY=float(phys["air_density_kg_m3"]),
            GRAVITY=float(phys["gravity_m_s2"]),
            WHEEL_CIRCUMFERENCE_M=float(phys["wheel_circ_m"]),
            FR_SPLIT_FRONT_PCT=float(phys["fr_split_front_pct"]),
            # Active bike name (or None for flat-physics profiles)
            DEFAULT_BIKE=p.get("default_bike"),
        )
    return _CONSTANTS_CACHE


def __getattr__(name: str) -> Any:  # PEP 562 module-level __getattr__
    consts = _constants()
    if name in consts:
        return consts[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def power_zone_bounds() -> list[tuple[str, int, int]]:
    """Materialise the eight power zones for this profile's FTP/MAP/AC/NM.

    Returns a list of (zone_name, watts_lower, watts_upper) inclusive bounds,
    derived from the formulas in CLAUDE.md. Adjacent zones touch.
    """
    c = _constants()
    ftp, map_w, ac, nm = c["FTP"], c["MAP_WORKING"], c["AC_FRESH_EST"], c["NM_PEAK"]
    return [
        ("Z1 Recovery",     0,                   round(ftp * 0.55) - 1),
        ("Z2 Endurance",    round(ftp * 0.55),   round(ftp * 0.70)),
        ("Z3 Tempo",        round(ftp * 0.71),   round(ftp * 0.90)),
        ("Z4 Sweet Spot",   round(ftp * 0.85),   round(ftp * 0.95)),
        ("Z5 Threshold",    round(ftp * 0.92),   ftp),
        ("Z6 MAP",          ftp + 1,             map_w),
        ("Z7 AC",           map_w + 1,           ac),
        ("Z8 NM",           ac + 1,              nm),
    ]


# ---------------------------------------------------------------------------
# Riding-partner registry helpers
#
# Peers live in USER_PROFILE.md frontmatter as flat sections named
# `peer_<name>:` (e.g. `peer_thomas:`).
# ---------------------------------------------------------------------------


def load_peer(name: str) -> dict[str, Any] | None:
    """Return the config dict for peer `<name>`, or None if not registered."""
    key = f"peer_{name.lower()}"
    cfg = load_profile().get(key)
    return dict(cfg) if cfg else None


def list_peers() -> list[str]:
    """Return registered peer short names (without the `peer_` prefix)."""
    return sorted(
        k[len("peer_"):] for k in load_profile().keys() if k.startswith("peer_")
    )


if __name__ == "__main__":
    import json
    p = load_profile()
    print("Loaded profile (with defaults filled in):")
    print(json.dumps(p, indent=2, default=str))
    print("\nMaterialised power zones (W):")
    for name, lo, hi in power_zone_bounds():
        print(f"  {name:<18} {lo:>4} – {hi:<4}")
    peers = list_peers()
    if peers:
        print(f"\nRegistered peers ({len(peers)}): {', '.join(peers)}")
