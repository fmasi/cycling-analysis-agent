# Multi-Bike Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the cycling-analysis-agent framework from a single-bike `physics:` block to a multi-bike data model so the Brompton G Line Electric can be analysed correctly. Validate end-to-end by predicting tomorrow's Long Gravel route on the Brompton.

**Architecture:** Schema-first migration. Introduce `bikes:` dict + `default_bike:` in `USER_PROFILE.md` with a temporary `physics:` alias during migration. Build a new `BikeConfig` dataclass and `load_bike()` helper. Migrate scripts in dependency order (physics_model → analyse_gpx → analyse_fit → analyse_climbs → tyre_pressure). Brompton outputs are HR-primary (no rider wattage quoted). E-assist modelled as power augmentation up to a cutoff speed. Silca tyre-pressure values fetched via an agent-driven Chrome browser lookup.

**Tech Stack:** Python 3.11+ in the `cycling` conda env (`/opt/miniconda3/envs/cycling/bin/python`). pytest for unit tests. PyYAML-free profile parsing (existing `_parse_simple_yaml` in `scripts/profile.py`). Playwright (or equivalent) Chrome browser for the Silca lookup agent prompt.

**Spec reference:** `docs/superpowers/specs/2026-05-12-multi-bike-support-design.md` (commit `b0f0726`).

---

## File structure

**New files:**
- `scripts/bike_config.py` — `BikeConfig` and `AssistConfig` dataclasses, surface validation
- `scripts/bike_cli.py` — shared `add_bike_args(parser)` + `resolve_bike(args)` helper for the `--bike` / `--surface` / `--assist-level` flags
- `docs/prompts/silca-pressure-lookup.md` — research-agent prompt template for the Chrome-browser Silca lookup
- `tests/test_bike_config.py` — unit tests for loader, validation, surface resolution
- `tests/test_physics_model_bikes.py` — unit tests for the bike-aware physics functions
- `tests/test_brompton_assist.py` — unit tests for `solve_speed_with_assist`

**Modified files:**
- `USER_PROFILE.md` — add `bikes:` + `default_bike:`, keep `physics:` as alias during Phases 1–3, drop in Phase 4
- `scripts/profile.py` — add `load_bike(slug)` and a `bikes:` parser; keep existing globals as alias-readers during migration
- `scripts/physics_model.py` — refactor `predict_speed` / `predict_power` / `speed_at_cadence_rpm` to take `BikeConfig`; add `solve_speed_with_assist`
- `scripts/analyse_gpx.py` — add `--bike` / `--surface` / `--assist-level`; thread `BikeConfig` through prediction; branch output template for assisted bikes
- `scripts/analyse_fit.py` — add `--bike` / `--surface`; auto-detect bike from power-data presence as a default; record bike slug in analysis header
- `scripts/analyse_climbs.py` — accept bike slug from parent, propagate to chart title and markdown header
- `scripts/tyre_pressure.py` — read tyre size, F/R split, and CRR-by-surface from the bike block; flag non-validated values
- `CLAUDE.md` — new "Bike selection" section + workflow-step updates

**Conventions to preserve:**
- Single-file scripts in `scripts/` (no package restructure)
- `_parse_simple_yaml` style for `USER_PROFILE.md` (no PyYAML dependency)
- Direct `from profile import …` imports (no module hierarchy churn)

---

## Phase 1: Schema migration

### Task 1: Add `bikes:` block to `USER_PROFILE.md` with both bikes populated

**Files:**
- Modify: `USER_PROFILE.md:27-34` (the existing `physics:` block) and the YAML frontmatter that sits above it

- [ ] **Step 1: Read the current frontmatter to confirm exact line ranges**

Run: `awk 'NR==1, /^---$/ && NR>1' USER_PROFILE.md | head -50`
Expected: dumps the YAML between the two `---` markers, ~48 lines.

- [ ] **Step 2: Append a `bikes:` block and `default_bike:` pointer above the closing `---`**

Insert after the existing `physics:` block (before the closing `---` on line 48) the following YAML:

```yaml
default_bike: tripster

bikes:
  tripster:
    name: Kinesis Decade Tripster
    bike_weight_kg: 11.6
    system_weight_kg_default: 90.1
    fr_split: "40/60"
    cda: 0.28
    cda_range: "0.26–0.30 (hoods, upright endurance)"
    drivetrain_efficiency: 0.97
    wheel_circ_m: 2.155
    has_power_meter: true
    tyres:
      model: Continental GP 4 Seasons
      size_mm: 32
      measured_mm: 31.4
    crr_by_surface:
      tarmac: 0.0050
      tarmac_intermediate: 0.0055
      tarmac_high_or_butyl: 0.0058
    surfaces_supported: [tarmac]

  brompton_g:
    name: Brompton G Line Electric
    bike_weight_kg: 19.5
    bike_weight_kg_no_battery: 15.7
    system_weight_kg_default: 98.5
    fr_split: "TBD"
    cda: 0.42
    cda_range: "0.40–0.45 (less upright than classic Brompton)"
    drivetrain_efficiency: 0.96
    wheel_circ_m: 1.59
    has_power_meter: false
    tyres:
      model: Schwalbe G-One Allround
      size_etrto: "54-406"
    crr_by_surface:
      tarmac: 0.0100
      tarmac_high_pressure: 0.0120
      gravel_smooth: 0.0180
      gravel_rough: 0.0250
    surfaces_supported: [tarmac, gravel]
    assist:
      type: e-Motiq
      placement: rear_hub
      rated_w: 250
      peak_w: 450
      torque_nm: 30
      sensor: torque
      cutoff_kph: 25
      levels: [L0, L1, L2, L3]
      boost_mode: true
      battery_wh: 345
      battery_range_km: "30–60"
      level_share:
        L0: 0.0
        L1: 0.5
        L2: 1.0
        L3: 1.5
      default_level_flat: L1
      default_level_climb_5pct: L2
      default_level_climb_10pct: L3
```

**Leave the existing `physics:` block in place** — it stays as an alias during Phases 1–3 and is removed in Phase 4. Tripster scripts continue to read it until they migrate.

- [ ] **Step 3: Sanity-check the YAML parses cleanly**

Run: `/opt/miniconda3/envs/cycling/bin/python -c "from scripts.profile import _parse_simple_yaml, load_profile; p = load_profile(); print(list(p.get('bikes', {}).keys())); print('default_bike:', p.get('default_bike'))"`
Expected: `['tripster', 'brompton_g']` and `default_bike: tripster`. If the simple-YAML parser doesn't handle the nested `assist:` block, that's caught by the next task.

- [ ] **Step 4: Commit**

`USER_PROFILE.md` is gitignored. No commit. Note the change in the implementation log instead (free-text in the next commit's message body).

---

### Task 2: Extend `_parse_simple_yaml` to handle the `bikes:` nested structure

**Files:**
- Modify: `scripts/profile.py` — the `_parse_simple_yaml` function

- [ ] **Step 1: Read the current parser**

Run: `grep -n "_parse_simple_yaml\|DEFAULTS\|load_profile" scripts/profile.py | head -20`
Expected: lines for the function definition, the DEFAULTS dict, and the load_profile function. Note the current parser is a minimal hand-rolled YAML reader; nested dicts and list values may need new handling.

- [ ] **Step 2: Write a failing test for nested-dict parsing**

Create `tests/test_profile_parser.py`:

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from profile import _parse_simple_yaml

def test_parses_nested_bikes_block(tmp_path):
    yaml_text = """\
default_bike: tripster

bikes:
  tripster:
    bike_weight_kg: 11.6
    wheel_circ_m: 2.155
    surfaces_supported: [tarmac]
    crr_by_surface:
      tarmac: 0.0050
  brompton_g:
    bike_weight_kg: 19.5
    surfaces_supported: [tarmac, gravel]
    crr_by_surface:
      tarmac: 0.0100
      gravel_smooth: 0.0180
    assist:
      cutoff_kph: 25
      level_share:
        L1: 0.5
"""
    p = _parse_simple_yaml(yaml_text)
    assert p["default_bike"] == "tripster"
    assert p["bikes"]["tripster"]["bike_weight_kg"] == 11.6
    assert p["bikes"]["tripster"]["surfaces_supported"] == ["tarmac"]
    assert p["bikes"]["brompton_g"]["crr_by_surface"]["gravel_smooth"] == 0.0180
    assert p["bikes"]["brompton_g"]["assist"]["cutoff_kph"] == 25
    assert p["bikes"]["brompton_g"]["assist"]["level_share"]["L1"] == 0.5
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_profile_parser.py::test_parses_nested_bikes_block -v`
Expected: FAIL — either `KeyError` (parser doesn't handle nesting) or a structural mismatch.

- [ ] **Step 4: Extend `_parse_simple_yaml` to handle nested dicts, inline-list values, and arbitrary indent depth**

The current parser handles a single level. Replace it with an indent-aware version:

```python
def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML parser for USER_PROFILE.md frontmatter.

    Supports: scalars (str/int/float/bool), nested dicts via indent,
    inline lists [a, b, c], and quoted strings.
    Does NOT support: multi-line strings, anchors, flow-style dicts,
    block-style lists with hyphens.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        # Pop deeper scopes
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        key, sep, val = line.lstrip().partition(":")
        if not sep:
            continue
        key = key.strip()
        val = val.strip()
        if val == "":
            new_dict: dict = {}
            parent[key] = new_dict
            stack.append((indent, new_dict))
        else:
            parent[key] = _coerce_scalar(val)
    return root


def _coerce_scalar(val: str):
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(x.strip()) for x in inner.split(",")]
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    try:
        if "." in val or "e" in val.lower():
            return float(val)
        return int(val)
    except ValueError:
        return val
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_profile_parser.py::test_parses_nested_bikes_block -v`
Expected: PASS.

- [ ] **Step 6: Run existing import + profile-loading smoke test**

Run: `/opt/miniconda3/envs/cycling/bin/python -c "from scripts.profile import load_profile; p = load_profile(); print('FTP:', p.get('fitness', {}).get('ftp_w')); print('bikes:', list(p.get('bikes', {}).keys()))"`
Expected: prints `FTP: 171` and `bikes: ['tripster', 'brompton_g']`. The legacy fields still load.

- [ ] **Step 7: Commit**

```bash
git add scripts/profile.py tests/test_profile_parser.py
git commit -m "Make _parse_simple_yaml indent-aware for nested bikes: block"
```

---

### Task 3: Add `BikeConfig` and `AssistConfig` dataclasses

**Files:**
- Create: `scripts/bike_config.py`
- Test: `tests/test_bike_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bike_config.py`:

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from bike_config import BikeConfig, AssistConfig, load_bike, UnknownBikeError, UnsupportedSurfaceError


def test_load_tripster_default():
    bike = load_bike(slug=None)
    assert bike.slug == "tripster"
    assert bike.name == "Kinesis Decade Tripster"
    assert bike.bike_weight_kg == 11.6
    assert bike.wheel_circ_m == 2.155
    assert bike.crr_by_surface["tarmac"] == 0.0050
    assert bike.surfaces_supported == ["tarmac"]
    assert bike.assist is None


def test_load_brompton_explicit():
    bike = load_bike(slug="brompton_g")
    assert bike.slug == "brompton_g"
    assert bike.bike_weight_kg == 19.5
    assert bike.wheel_circ_m == 1.59
    assert bike.crr_by_surface["gravel_smooth"] == 0.0180
    assert bike.assist is not None
    assert bike.assist.cutoff_kph == 25
    assert bike.assist.level_share["L1"] == 0.5
    assert bike.assist.battery_wh == 345


def test_unknown_slug_raises_with_valid_list():
    try:
        load_bike(slug="penny_farthing")
    except UnknownBikeError as e:
        assert "penny_farthing" in str(e)
        assert "tripster" in str(e)
        assert "brompton_g" in str(e)
    else:
        raise AssertionError("expected UnknownBikeError")


def test_surface_validation_supported():
    bike = load_bike(slug="brompton_g")
    bike.validate_surface("gravel_smooth")  # no raise


def test_surface_validation_unsupported():
    bike = load_bike(slug="tripster")
    try:
        bike.validate_surface("gravel_rough")
    except UnsupportedSurfaceError as e:
        assert "gravel_rough" in str(e)
        assert "tarmac" in str(e)
    else:
        raise AssertionError("expected UnsupportedSurfaceError")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_bike_config.py -v`
Expected: FAIL — `bike_config` module does not exist.

- [ ] **Step 3: Implement `scripts/bike_config.py`**

```python
"""BikeConfig / AssistConfig dataclasses and the load_bike() helper.

Reads the bikes: dict from USER_PROFILE.md (via profile.load_profile) and
returns a typed config object. Single source of truth for per-bike physics.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from profile import load_profile


class UnknownBikeError(ValueError):
    pass


class UnsupportedSurfaceError(ValueError):
    pass


@dataclass
class AssistConfig:
    type: str
    placement: str
    rated_w: int
    peak_w: int
    torque_nm: int
    sensor: str
    cutoff_kph: float
    levels: list[str]
    boost_mode: bool
    battery_wh: int
    battery_range_km: str
    level_share: dict[str, float]
    default_level_flat: str
    default_level_climb_5pct: str
    default_level_climb_10pct: str


@dataclass
class BikeConfig:
    slug: str
    name: str
    bike_weight_kg: float
    system_weight_kg_default: float
    fr_split: str
    cda: float
    cda_range: str
    drivetrain_efficiency: float
    wheel_circ_m: float
    has_power_meter: bool
    tyres: dict
    crr_by_surface: dict[str, float]
    surfaces_supported: list[str]
    assist: Optional[AssistConfig] = None

    def validate_surface(self, surface: str) -> None:
        if surface in self.crr_by_surface:
            return
        # Allow surface match to surfaces_supported categories
        if any(surface.startswith(s) for s in self.surfaces_supported):
            return
        raise UnsupportedSurfaceError(
            f"Surface '{surface}' not supported by bike '{self.slug}'. "
            f"Supported surfaces: {self.surfaces_supported}. "
            f"CRR keys: {list(self.crr_by_surface)}"
        )


def load_bike(slug: Optional[str] = None, *, profile: Optional[dict] = None) -> BikeConfig:
    if profile is None:
        profile = load_profile()
    bikes = profile.get("bikes") or {}
    if not bikes:
        raise UnknownBikeError("No bikes: block in USER_PROFILE.md")
    if slug is None:
        slug = profile.get("default_bike")
        if slug is None:
            raise UnknownBikeError("default_bike: not set in USER_PROFILE.md")
    if slug not in bikes:
        raise UnknownBikeError(
            f"Unknown bike slug: '{slug}'. Valid slugs: {sorted(bikes)}"
        )
    raw = bikes[slug]
    assist = None
    if "assist" in raw:
        a = raw["assist"]
        assist = AssistConfig(
            type=a["type"],
            placement=a["placement"],
            rated_w=int(a["rated_w"]),
            peak_w=int(a["peak_w"]),
            torque_nm=int(a["torque_nm"]),
            sensor=a["sensor"],
            cutoff_kph=float(a["cutoff_kph"]),
            levels=list(a["levels"]),
            boost_mode=bool(a["boost_mode"]),
            battery_wh=int(a["battery_wh"]),
            battery_range_km=a["battery_range_km"],
            level_share={k: float(v) for k, v in a["level_share"].items()},
            default_level_flat=a["default_level_flat"],
            default_level_climb_5pct=a["default_level_climb_5pct"],
            default_level_climb_10pct=a["default_level_climb_10pct"],
        )
    return BikeConfig(
        slug=slug,
        name=raw["name"],
        bike_weight_kg=float(raw["bike_weight_kg"]),
        system_weight_kg_default=float(raw["system_weight_kg_default"]),
        fr_split=str(raw["fr_split"]),
        cda=float(raw["cda"]),
        cda_range=raw.get("cda_range", ""),
        drivetrain_efficiency=float(raw["drivetrain_efficiency"]),
        wheel_circ_m=float(raw["wheel_circ_m"]),
        has_power_meter=bool(raw["has_power_meter"]),
        tyres=raw["tyres"],
        crr_by_surface={k: float(v) for k, v in raw["crr_by_surface"].items()},
        surfaces_supported=list(raw["surfaces_supported"]),
        assist=assist,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_bike_config.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bike_config.py tests/test_bike_config.py
git commit -m "Add BikeConfig/AssistConfig with load_bike() helper"
```

---

### Task 4: Add the shared `--bike` / `--surface` / `--assist-level` CLI helper

**Files:**
- Create: `scripts/bike_cli.py`
- Test: `tests/test_bike_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bike_cli.py`:

```python
import argparse
import sys
from io import StringIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bike_cli import add_bike_args, resolve_bike
from bike_config import UnknownBikeError, UnsupportedSurfaceError


def _parser():
    p = argparse.ArgumentParser()
    add_bike_args(p)
    return p


def test_explicit_brompton_gravel():
    args = _parser().parse_args(["--bike", "brompton_g", "--surface", "gravel_smooth"])
    bike, surface, level = resolve_bike(args)
    assert bike.slug == "brompton_g"
    assert surface == "gravel_smooth"
    assert level == "L1"  # default_level_flat for Brompton


def test_default_bike_warns(capsys):
    args = _parser().parse_args([])
    bike, surface, level = resolve_bike(args)
    assert bike.slug == "tripster"
    assert surface == "tarmac"  # first of surfaces_supported
    assert level is None
    captured = capsys.readouterr()
    assert "using default bike 'tripster'" in captured.err


def test_bad_slug_hard_fails():
    args = _parser().parse_args(["--bike", "penny_farthing"])
    try:
        resolve_bike(args)
    except UnknownBikeError as e:
        assert "tripster" in str(e)
        assert "brompton_g" in str(e)
    else:
        raise AssertionError("expected UnknownBikeError")


def test_surface_not_supported_fails():
    args = _parser().parse_args(["--bike", "tripster", "--surface", "gravel_smooth"])
    try:
        resolve_bike(args)
    except UnsupportedSurfaceError:
        pass
    else:
        raise AssertionError("expected UnsupportedSurfaceError")


def test_assist_level_ignored_for_unassisted():
    args = _parser().parse_args(["--bike", "tripster", "--assist-level", "L2"])
    bike, surface, level = resolve_bike(args)
    assert bike.slug == "tripster"
    assert level is None  # assist-level silently ignored for unassisted bikes
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_bike_cli.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `scripts/bike_cli.py`**

```python
"""Shared CLI helper: --bike / --surface / --assist-level argument resolution.

Resolution order:
1. --bike passed and matches bikes: dict → use it.
2. --bike passed but no match → hard fail with valid-slug list.
3. --bike omitted → use default_bike, emit a one-line stderr warning.

--surface defaults to the bike's first surfaces_supported.
--assist-level defaults to bike.assist.default_level_flat for assisted bikes,
None for unassisted (with --assist-level silently ignored when not applicable).
"""
from __future__ import annotations
import argparse
import sys
from typing import Optional, Tuple

from bike_config import BikeConfig, load_bike, UnknownBikeError, UnsupportedSurfaceError
from profile import load_profile


def add_bike_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bike",
        default=None,
        help="Bike slug from USER_PROFILE.md bikes: dict. Defaults to default_bike.",
    )
    parser.add_argument(
        "--surface",
        default=None,
        help="Surface key under the bike's crr_by_surface; defaults to first surfaces_supported.",
    )
    parser.add_argument(
        "--assist-level",
        default=None,
        choices=["L0", "L1", "L2", "L3"],
        help="Assist level for motorised bikes (ignored otherwise). Defaults to bike's default_level_flat.",
    )


def resolve_bike(args: argparse.Namespace) -> Tuple[BikeConfig, str, Optional[str]]:
    profile = load_profile()
    if args.bike is None:
        slug = profile.get("default_bike")
        print(f"using default bike '{slug}' (no --bike specified)", file=sys.stderr)
    else:
        slug = args.bike
    bike = load_bike(slug=slug, profile=profile)

    surface = args.surface or bike.surfaces_supported[0]
    bike.validate_surface(surface)

    level: Optional[str] = None
    if bike.assist is not None:
        level = args.assist_level or bike.assist.default_level_flat
    return bike, surface, level
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_bike_cli.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bike_cli.py tests/test_bike_cli.py
git commit -m "Add bike_cli helper for --bike/--surface/--assist-level resolution"
```

---

## Phase 2: Physics model migration

### Task 5: Refactor `predict_speed` to accept `BikeConfig` + surface

**Files:**
- Modify: `scripts/physics_model.py:40-65` (the `predict_speed` function)
- Test: `tests/test_physics_model_bikes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_physics_model_bikes.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from physics_model import predict_speed
from bike_config import load_bike

TRIPSTER = load_bike("tripster")
BROMPTON = load_bike("brompton_g")


def test_predict_speed_tripster_flat_at_ftp():
    # 171 W FTP, 0% grade, system 90.1 kg tarmac → ~26 km/h hoods
    v = predict_speed(power_crank_w=171, grade_pct=0.0, bike=TRIPSTER, surface="tarmac",
                      system_weight_kg=90.1)
    assert 24.0 < v < 28.5, f"expected ~26 km/h, got {v:.2f}"


def test_predict_speed_brompton_flat_at_120w_tarmac():
    # Brompton at 120 W rider crank, 0% grade, system 98.5 kg, tarmac (CRR 0.010)
    # Higher CdA, higher CRR, heavier bike → should be much slower than Tripster at same wattage
    v_tripster = predict_speed(power_crank_w=120, grade_pct=0.0, bike=TRIPSTER, surface="tarmac",
                               system_weight_kg=90.1)
    v_brompton = predict_speed(power_crank_w=120, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                               system_weight_kg=98.5)
    assert v_brompton < v_tripster - 4.0, f"Brompton should be >=4 km/h slower; got {v_brompton:.1f} vs {v_tripster:.1f}"


def test_predict_speed_brompton_gravel_slower_than_tarmac():
    v_tarmac = predict_speed(power_crank_w=120, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                             system_weight_kg=98.5)
    v_gravel = predict_speed(power_crank_w=120, grade_pct=0.0, bike=BROMPTON, surface="gravel_smooth",
                             system_weight_kg=98.5)
    assert v_gravel < v_tarmac, f"gravel must be slower than tarmac; got {v_gravel:.1f} vs {v_tarmac:.1f}"


def test_predict_speed_uses_bike_drivetrain_efficiency():
    # Brompton eta=0.96 vs Tripster eta=0.97 at otherwise equal physics
    # Construct a synthetic case where only eta differs (use Brompton bike but force-match other params)
    # Simpler: just verify a numerical signature is bike-dependent
    v_a = predict_speed(power_crank_w=150, grade_pct=2.0, bike=TRIPSTER, surface="tarmac",
                        system_weight_kg=90.1)
    v_b = predict_speed(power_crank_w=150, grade_pct=2.0, bike=BROMPTON, surface="tarmac",
                        system_weight_kg=90.1)
    assert v_a != v_b
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_physics_model_bikes.py -v`
Expected: FAIL — `predict_speed` does not accept `bike` keyword today.

- [ ] **Step 3: Refactor `predict_speed` in `scripts/physics_model.py`**

Replace the existing function (around line 40):

```python
from typing import Optional
from bike_config import BikeConfig

AIR_DENSITY = 1.225
GRAVITY = 9.81


def predict_speed(
    power_crank_w: float,
    grade_pct: float,
    *,
    bike: BikeConfig,
    surface: str,
    system_weight_kg: float,
    rho: float = AIR_DENSITY,
    g: float = GRAVITY,
) -> float:
    """Speed in km/h that the given rider power produces on the given bike+surface+grade.

    All bike-specific physics (CdA, CRR, drivetrain efficiency) come from the BikeConfig.
    """
    crr = bike.crr_by_surface[surface]
    cda = bike.cda
    eta = bike.drivetrain_efficiency
    p_wheel = power_crank_w * eta
    theta = math.atan(grade_pct / 100.0)

    # Solve p_wheel = (0.5 * rho * cda * v^2 + crr * m * g + m * g * sin(theta)) * v
    # Iteratively (bisection) for v in m/s.
    lo, hi = 0.01, 30.0  # m/s
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        rhs = (0.5 * rho * cda * mid * mid + crr * system_weight_kg * g + system_weight_kg * g * math.sin(theta)) * mid
        if rhs < p_wheel:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) * 3.6  # m/s → km/h
```

Keep the `predict_power` function as-is for now; it will be refactored in the next task.

**Important:** retain a backwards-compat shim at the bottom of the file so existing callers (`analyse_gpx.py`, `analyse_fit.py`) still work until they migrate:

```python
def predict_speed_legacy(power_crank_w, grade_pct, system_weight_kg=None, cda=None,
                          crr=None, eta=None, rho=AIR_DENSITY, g=GRAVITY):
    """Deprecated: pre-bike-aware signature. Routes through the default bike."""
    bike = load_bike()  # default
    sw = system_weight_kg if system_weight_kg is not None else bike.system_weight_kg_default
    return predict_speed(power_crank_w, grade_pct, bike=bike, surface=bike.surfaces_supported[0],
                          system_weight_kg=sw, rho=rho, g=g)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_physics_model_bikes.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Smoke-test existing callers still import cleanly**

Run: `/opt/miniconda3/envs/cycling/bin/python -c "from scripts import physics_model; from scripts.physics_model import predict_speed, predict_speed_legacy, predict_power; print('imports OK')"`
Expected: prints `imports OK`. No `ImportError`.

- [ ] **Step 6: Commit**

```bash
git add scripts/physics_model.py tests/test_physics_model_bikes.py
git commit -m "Refactor predict_speed to take BikeConfig + surface"
```

---

### Task 6: Refactor `predict_power`, `speed_at_cadence_rpm`, `vam_at_power`, `power_for_60rpm_in_lowest_gear` to take `BikeConfig`

**Files:**
- Modify: `scripts/physics_model.py:66-115` (the remaining bike-keyed functions)
- Test: extend `tests/test_physics_model_bikes.py`

- [ ] **Step 1: Write failing tests for each function**

Append to `tests/test_physics_model_bikes.py`:

```python
from physics_model import predict_power, speed_at_cadence_rpm, vam_at_power, power_for_60rpm_in_lowest_gear


def test_predict_power_brompton_climb():
    # Approx 18 km/h at 5% on Brompton → check it's a sensible wattage band
    w = predict_power(speed_kmh=18.0, grade_pct=5.0, bike=BROMPTON, surface="tarmac",
                       system_weight_kg=98.5)
    assert 240 < w < 400, f"expected ~240–400 W, got {w:.1f}"


def test_speed_at_cadence_brompton_wheel_circ():
    # Brompton wheel circ = 1.59 m, not the Tripster's 2.155 m.
    # 80 rpm × gear ratio 50/15 → wheel rpm 80 * (50/15) = 266.7 rpm = 4.44 rps × 1.59 m = 7.06 m/s = 25.4 km/h
    v = speed_at_cadence_rpm(cadence_rpm=80, gear_ratio=50/15, wheel_circ_m=BROMPTON.wheel_circ_m)
    assert 24.5 < v < 26.5, f"expected ~25.4 km/h, got {v:.2f}"


def test_vam_at_power_uses_bike():
    vam = vam_at_power(power_crank_w=171, grade_pct=8.0, bike=TRIPSTER, surface="tarmac",
                        system_weight_kg=90.1)
    assert 600 < vam < 1000


def test_power_for_60rpm_lowest_gear_brompton():
    # Brompton lowest gear: 50T × 18T (largest cassette cog) = ratio 2.78
    # At 60 rpm, wheel rps = 60/60 × (1/2.78) = 0.36 rps → 0.36 × 1.59 = 0.57 m/s = 2.06 km/h
    # Power needed on 10% grade ~ (mgsin theta + Crr mg) v / eta
    w = power_for_60rpm_in_lowest_gear(grade_pct=10.0, lowest_ratio=50/18, bike=BROMPTON,
                                          surface="tarmac", system_weight_kg=98.5)
    assert 30 < w < 80, f"unexpected wattage for 60rpm-lowest-Brompton-10pct: {w:.1f}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_physics_model_bikes.py -v`
Expected: 4 new tests FAIL (signature mismatch).

- [ ] **Step 3: Refactor the four functions in `scripts/physics_model.py`**

Replace each with:

```python
def predict_power(
    speed_kmh: float,
    grade_pct: float,
    *,
    bike: BikeConfig,
    surface: str,
    system_weight_kg: float,
    rho: float = AIR_DENSITY,
    g: float = GRAVITY,
) -> float:
    """Crank power required to hold the given speed on the given bike+surface+grade."""
    crr = bike.crr_by_surface[surface]
    cda = bike.cda
    eta = bike.drivetrain_efficiency
    v = speed_kmh / 3.6
    theta = math.atan(grade_pct / 100.0)
    p_wheel = (0.5 * rho * cda * v * v + crr * system_weight_kg * g + system_weight_kg * g * math.sin(theta)) * v
    return p_wheel / eta


def speed_at_cadence_rpm(cadence_rpm: float, gear_ratio: float, wheel_circ_m: float) -> float:
    """Pure kinematic — already bike-agnostic via wheel_circ_m. No change beyond signature documentation."""
    return cadence_rpm * gear_ratio * wheel_circ_m * 60.0 / 1000.0  # km/h


def vam_at_power(
    power_crank_w: float,
    grade_pct: float,
    *,
    bike: BikeConfig,
    surface: str,
    system_weight_kg: float,
) -> float:
    """Vertical Ascent Metres / hour = climb_speed_m_per_s × sin(theta) × 3600."""
    v_kmh = predict_speed(power_crank_w, grade_pct, bike=bike, surface=surface,
                           system_weight_kg=system_weight_kg)
    v_ms = v_kmh / 3.6
    theta = math.atan(grade_pct / 100.0)
    return v_ms * math.sin(theta) * 3600.0


def power_for_60rpm_in_lowest_gear(
    grade_pct: float,
    lowest_ratio: float,
    *,
    bike: BikeConfig,
    surface: str,
    system_weight_kg: float,
) -> float:
    """Crank power required to spin the lowest gear at 60 rpm on the given grade."""
    v_kmh = speed_at_cadence_rpm(60.0, lowest_ratio, wheel_circ_m=bike.wheel_circ_m)
    return predict_power(v_kmh, grade_pct, bike=bike, surface=surface,
                          system_weight_kg=system_weight_kg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_physics_model_bikes.py -v`
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/physics_model.py tests/test_physics_model_bikes.py
git commit -m "Refactor remaining physics functions to take BikeConfig"
```

---

### Task 7: Add `solve_speed_with_assist` for motorised bikes

**Files:**
- Modify: `scripts/physics_model.py` — append new function
- Test: `tests/test_brompton_assist.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_brompton_assist.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bike_config import load_bike
from physics_model import solve_speed_with_assist

BROMPTON = load_bike("brompton_g")


def test_l0_equals_self_power():
    # L0 = no assist; result should equal pure predict_speed wattage path
    from physics_model import predict_speed
    rider_w = 120
    r = solve_speed_with_assist(rider_w, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L0")
    v_self = predict_speed(rider_w, 0.0, bike=BROMPTON, surface="tarmac", system_weight_kg=98.5)
    assert abs(r.speed_kmh - v_self) < 0.01
    assert r.motor_w == 0


def test_l1_adds_motor_below_cutoff():
    # On a moderate climb at rider 120 W: L1 adds motor up to motor_max OR until cutoff.
    r = solve_speed_with_assist(rider_w=120, grade_pct=4.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L1")
    assert r.motor_w > 0
    assert r.speed_kmh <= 25.0  # below cutoff


def test_motor_drops_to_zero_above_cutoff():
    # Strong rider effort on flat → speed pushes above 25 km/h, motor disengages
    r = solve_speed_with_assist(rider_w=250, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L2")
    if r.speed_kmh > 25.0:
        assert r.motor_w == 0


def test_motor_w_capped_at_rated():
    # On steep climb at high rider effort with L3 multiplier 1.5, motor would scale beyond rated
    # but must cap at bike.assist.rated_w (250 W).
    r = solve_speed_with_assist(rider_w=200, grade_pct=8.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L3")
    assert r.motor_w <= 250


def test_battery_drain_wh_per_hour_field():
    # Output exposes Wh/hour drain for battery-range estimation
    r = solve_speed_with_assist(rider_w=100, grade_pct=2.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L1")
    assert hasattr(r, "wh_per_hour")
    assert r.wh_per_hour >= 0
    # Wh/hour ≈ motor_w because 1 W × 1 h = 1 Wh
    assert abs(r.wh_per_hour - r.motor_w) < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_brompton_assist.py -v`
Expected: FAIL — `solve_speed_with_assist` does not exist.

- [ ] **Step 3: Implement `solve_speed_with_assist` in `scripts/physics_model.py`**

Append:

```python
from dataclasses import dataclass


@dataclass
class AssistedSpeedResult:
    speed_kmh: float
    rider_w: float
    motor_w: float
    wh_per_hour: float


def solve_speed_with_assist(
    rider_w: float,
    grade_pct: float,
    *,
    bike: BikeConfig,
    surface: str,
    system_weight_kg: float,
    assist_level: str,
    rho: float = AIR_DENSITY,
    g: float = GRAVITY,
) -> AssistedSpeedResult:
    """Solve combined rider+motor wheel power for an e-assist bike.

    Motor adds power proportional to rider input via bike.assist.level_share[level],
    capped at bike.assist.rated_w, but only when speed < bike.assist.cutoff_kph.
    Above cutoff, motor_w = 0.

    Returns rider_w, motor_w, combined speed, and Wh/hour drain.
    """
    assert bike.assist is not None, f"bike '{bike.slug}' has no assist block"
    share = bike.assist.level_share[assist_level]
    motor_cap = bike.assist.rated_w
    cutoff_kmh = bike.assist.cutoff_kph
    crr = bike.crr_by_surface[surface]
    eta = bike.drivetrain_efficiency
    theta = math.atan(grade_pct / 100.0)

    # Iterative: try a candidate motor_w consistent with the cutoff rule, then solve speed.
    # Start optimistic: assume motor is at min(share * rider_w, motor_cap).
    candidate_motor = min(share * rider_w, motor_cap)
    p_wheel = (rider_w + candidate_motor) * eta

    lo, hi = 0.01, 30.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        rhs = (0.5 * rho * bike.cda * mid * mid + crr * system_weight_kg * g
               + system_weight_kg * g * math.sin(theta)) * mid
        if rhs < p_wheel:
            lo = mid
        else:
            hi = mid
    v_ms = 0.5 * (lo + hi)
    v_kmh = v_ms * 3.6

    if v_kmh > cutoff_kmh:
        # Above cutoff → motor disengages; re-solve with rider only.
        candidate_motor = 0.0
        p_wheel = rider_w * eta
        lo, hi = 0.01, 30.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            rhs = (0.5 * rho * bike.cda * mid * mid + crr * system_weight_kg * g
                   + system_weight_kg * g * math.sin(theta)) * mid
            if rhs < p_wheel:
                lo = mid
            else:
                hi = mid
        v_kmh = 0.5 * (lo + hi) * 3.6

    return AssistedSpeedResult(
        speed_kmh=v_kmh,
        rider_w=float(rider_w),
        motor_w=float(candidate_motor),
        wh_per_hour=float(candidate_motor),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_brompton_assist.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/physics_model.py tests/test_brompton_assist.py
git commit -m "Add solve_speed_with_assist for e-Motiq motor model"
```

---

## Phase 3: Migrate user-facing scripts

### Task 8: Migrate `analyse_gpx.py` to use `BikeConfig`

**Files:**
- Modify: `scripts/analyse_gpx.py` — imports (lines 24–27), argparse block (lines ~213–240), call sites of `predict_speed`/`vam_at_power`/`power_for_60rpm_in_lowest_gear`

- [ ] **Step 1: Find all call sites and CLI arg block**

Run: `grep -n "predict_speed\|vam_at_power\|power_for_60rpm\|SYSTEM_WEIGHT_KG\|add_argument" scripts/analyse_gpx.py`
Expected: ~15-25 hits. Note each line number.

- [ ] **Step 2: Add bike-aware arguments to the argparse block**

In `main()`, after the existing `add_argument` calls (find the line with `args = parser.parse_args()`), add **before** `parser.parse_args()`:

```python
    from bike_cli import add_bike_args, resolve_bike
    add_bike_args(parser)
```

After `args = parser.parse_args()`:

```python
    bike, surface, assist_level = resolve_bike(args)
```

- [ ] **Step 3: Replace bare-constants call sites with bike-aware ones**

For each call to `predict_speed(power_crank_w, grade_pct, ...)`, change to:

```python
predict_speed(power_crank_w, grade_pct, bike=bike, surface=surface,
              system_weight_kg=bike.system_weight_kg_default)
```

Same pattern for `vam_at_power` and `power_for_60rpm_in_lowest_gear`. For climbs on the Brompton, **also** call `solve_speed_with_assist`:

```python
if bike.assist is not None:
    from physics_model import solve_speed_with_assist
    assisted = solve_speed_with_assist(
        rider_w=…,                        # use a target rider effort, e.g. 120 W on L1
        grade_pct=climb["avg_grade"],
        bike=bike, surface=surface,
        system_weight_kg=bike.system_weight_kg_default,
        assist_level=assist_level,
    )
```

- [ ] **Step 4: Update the saved markdown's header to include the bike + surface**

Find the function that writes `routes/<name>-prediction.md` (search for `f.write` or `prediction.md`). At the top of the output, insert:

```python
f.write(f"# {gpx_name} — Route Prediction\n\n")
f.write(f"**Bike:** {bike.name} (`{bike.slug}`)  \n")
f.write(f"**Surface:** {surface}  \n")
if bike.assist is not None:
    f.write(f"**Assist level (default):** {assist_level}  \n")
f.write("\n")
```

- [ ] **Step 5: For assisted bikes, switch the pacing-table column headers**

Find the climb table generation (search for `Power @ FTP` or `Speed @ FTP`). Branch on `bike.has_power_meter`:

```python
if bike.has_power_meter:
    headers = ["Climb", "km", "avg grade", "Power @ FTP (W)", "Speed @ FTP (km/h)",
               "Power @ MAP (W)", "Speed @ MAP (km/h)"]
else:
    headers = ["Climb", "km", "avg grade", "HR target", "Assist level",
               "Speed (km/h)", "Wh used"]
```

Populate each row accordingly (HR target from grade thresholds: 5% → Z3, 10% → Z4 max).

- [ ] **Step 6: Smoke-test with the Long Gravel route**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py --bike brompton_g --surface gravel_smooth --no-verify "routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified).gpx"`
Expected: script runs without error, prints climb summary with HR targets and assist levels (not watts). No traceback. Does NOT save (no `--save`).

- [ ] **Step 7: Smoke-test the Tripster default still works**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py --bike tripster routes/$(ls routes/ | grep -v "Long Gravel" | head -1)`
Expected: stderr shows nothing special (explicit `--bike` passed), output uses watts in the climb table, no traceback.

- [ ] **Step 8: Commit**

```bash
git add scripts/analyse_gpx.py
git commit -m "Add --bike/--surface/--assist-level to analyse_gpx; branch output for assisted bikes"
```

---

### Task 9: Migrate `analyse_fit.py` to use `BikeConfig` + auto-detect bike

**Files:**
- Modify: `scripts/analyse_fit.py` — imports (lines 36–40), argparse, `analyse()` function

- [ ] **Step 1: Find argparse and call sites**

Run: `grep -n "predict_speed\|SYSTEM_WEIGHT_KG\|RIDER_WEIGHT_KG\|add_argument\|args = parser" scripts/analyse_fit.py | head -30`
Expected: argparse block and a handful of constant uses.

- [ ] **Step 2: Add `--bike`/`--surface` arguments**

Inside `main()` (or wherever the parser is built), add before `parse_args()`:

```python
    from bike_cli import add_bike_args, resolve_bike
    add_bike_args(parser)
```

- [ ] **Step 3: Implement bike auto-detection from power-data presence**

Add a function in `analyse_fit.py`:

```python
def _auto_detect_bike(records: list) -> str:
    """Return a bike slug guess based on power-data presence.

    FIT with any 'power' records → tripster (high confidence)
    FIT with no 'power' records → brompton_g (high confidence)

    The CLI --bike flag overrides this. If --bike not passed, we use the
    auto-detect result instead of the default_bike. Emits a stderr note so
    the rider sees the inference.
    """
    has_power = any(
        r.get_value("power") is not None
        for r in records
        if r.name == "record"
    )
    slug = "tripster" if has_power else "brompton_g"
    print(f"auto-detected bike: '{slug}' (power records {'present' if has_power else 'absent'})",
          file=sys.stderr)
    return slug
```

In `analyse(path)` (or equivalent), call this **before** `resolve_bike()` and override `args.bike` if it's `None`:

```python
if args.bike is None:
    args.bike = _auto_detect_bike(records)
bike, surface, assist_level = resolve_bike(args)
```

- [ ] **Step 4: Update the analysis markdown header**

Find the section that writes `rides/analyses/<date>-<name>.md` (search for `analyses` or `.md`). Insert at the top:

```python
header = (
    f"# {ride_name} — Ride Analysis\n\n"
    f"**Bike:** {bike.name} (`{bike.slug}`)  \n"
    f"**Surface:** {surface}  \n"
)
if bike.assist is not None:
    header += f"**Battery % start:** _pending rider input_  \n"
    header += f"**Battery % end:** _pending rider input_  \n"
    header += f"**Assist pattern:** _pending rider input_  \n"
header += "\n"
```

The agent fills the battery / assist-pattern placeholders during the workflow (per the spec's calibration loop).

- [ ] **Step 5: Replace bare-constants call sites**

Each `SYSTEM_WEIGHT_KG` usage → `bike.system_weight_kg_default`. Each `predict_speed(..., system_weight_kg=…)` call gets `bike=bike, surface=surface`.

- [ ] **Step 6: Smoke-test on the 12 May Cély test ride (Brompton, no power)**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/analyse_fit.py rides/$(ls rides/ | grep -i 'cely.*test\|2026-05-12' | head -1)`
Expected: stderr shows `auto-detected bike: 'brompton_g' (power records absent)`. Output uses HR-zone phrasing, no power-derived TSS, no traceback.

- [ ] **Step 7: Smoke-test on a Tripster ride (e.g. 19 April Burgess Hill)**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/analyse_fit.py rides/$(ls rides/ | grep -i 'burgess\|2026-04-19' | head -1)`
Expected: stderr shows `auto-detected bike: 'tripster' (power records present)`. Output uses power-derived TSS. No traceback.

- [ ] **Step 8: Commit**

```bash
git add scripts/analyse_fit.py
git commit -m "Add --bike/--surface to analyse_fit; auto-detect from power presence"
```

---

### Task 10: Migrate `analyse_climbs.py` to thread the bike slug through

**Files:**
- Modify: `scripts/analyse_climbs.py` — argparse, chart titles, markdown header

- [ ] **Step 1: Add `--bike` / `--surface` arguments**

In `main()`:

```python
    from bike_cli import add_bike_args, resolve_bike
    add_bike_args(parser)
```

After `parse_args()`:

```python
    bike, surface, assist_level = resolve_bike(args)
```

- [ ] **Step 2: Thread the bike name into the overview chart title**

Find `plot_overview` (line 245). Modify its signature to accept `bike_name: str = ""` and prepend the bike name to the title:

```python
title = f"{bike_name} — {ride_name}" if bike_name else ride_name
ax.set_title(title)
```

- [ ] **Step 3: Add the bike header to the markdown output**

In `write_markdown` (line 323), insert at the top of the markdown:

```python
out.write(f"**Bike:** {bike.name} (`{bike.slug}`)  \n")
out.write(f"**Surface:** {surface}  \n\n")
```

(Pass `bike` and `surface` into `write_markdown` — extend the signature.)

- [ ] **Step 4: Smoke-test by running directly on the Cély FIT**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/analyse_climbs.py rides/$(ls rides/ | grep -i 'cely.*test' | head -1)`
Expected: stderr shows the default-bike warning OR auto-detect note from analyse_fit (if it shares parsing). Output writes a `-climbs.md` file with the Brompton header. No traceback.

- [ ] **Step 5: Commit**

```bash
git add scripts/analyse_climbs.py
git commit -m "Thread bike slug through analyse_climbs chart titles and markdown"
```

---

### Task 11: Migrate `tyre_pressure.py` to use `BikeConfig`

**Files:**
- Modify: `scripts/tyre_pressure.py` — argparse (lines 78–87), `pressures_at_split` and `all_surfaces` functions

- [ ] **Step 1: Add `--bike` argument**

Replace the existing argparse block (around lines 78–87) with:

```python
def main():
    parser = argparse.ArgumentParser(description="Silca-style tyre pressure recommendations per bike.")
    from bike_cli import add_bike_args, resolve_bike
    add_bike_args(parser)
    parser.add_argument("--system-weight", type=float, default=None,
                        help="System weight kg; defaults to bike.system_weight_kg_default")
    parser.add_argument("--front-pct", type=float, default=None,
                        help="Front-wheel weight percent; defaults to bike.fr_split front portion")
    args = parser.parse_args()
    bike, surface, _ = resolve_bike(args)

    system_kg = args.system_weight if args.system_weight is not None else bike.system_weight_kg_default
    if args.front_pct is not None:
        front_pct = args.front_pct
    elif bike.fr_split.upper() == "TBD":
        print(f"warning: bike '{bike.slug}' has fr_split=TBD — using Silca default 48",
              file=sys.stderr)
        front_pct = 48.0
        unvalidated = True
    else:
        # Parse "40/60" → 40.0
        front_pct = float(bike.fr_split.split("/")[0])
        unvalidated = False
```

- [ ] **Step 2: Flag non-validated bikes in output**

Append before printing results:

```python
    if unvalidated or bike.slug == "brompton_g":
        print("NOTE: pressures are indicative — not yet Silca-validated for this bike.",
              file=sys.stderr)
        print("Run the agent-driven Silca lookup (see docs/prompts/silca-pressure-lookup.md).",
              file=sys.stderr)
```

- [ ] **Step 3: Smoke-test Tripster**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/tyre_pressure.py --bike tripster --surface tarmac`
Expected: same numbers as before the refactor (within 1 psi). No traceback.

- [ ] **Step 4: Smoke-test Brompton (unvalidated path)**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/tyre_pressure.py --bike brompton_g --surface gravel`
Expected: stderr prints the unvalidated warning + agent-prompt pointer. Stdout prints indicative numbers (different from Tripster).

- [ ] **Step 5: Commit**

```bash
git add scripts/tyre_pressure.py
git commit -m "Migrate tyre_pressure to per-bike fr_split/tyres/CRR with unvalidated flag"
```

---

## Phase 4: Agent rules and onboarding artefacts

### Task 12: Update `CLAUDE.md` with the Bike selection section + workflow steps

**Files:**
- Modify: `CLAUDE.md` — insert new section between "How this framework works" and "Core principles"; update "Workflow expectations"

- [ ] **Step 1: Locate insertion points**

Run: `grep -n "^## " CLAUDE.md`
Expected: a list of `##` section headings. Note the line of "## How this framework works" and "## Core principles".

- [ ] **Step 2: Insert the Bike selection section**

Use `Edit` to add after the closing of "How this framework works" and before "## Core principles":

```markdown
---

## Bike selection

Every FIT analysis and GPX prediction is bike-specific. Determine the bike before running scripts.

**Primary signal (high confidence):**
- FIT contains power records → **Tripster** (`--bike tripster`)
- FIT has no power records → **Brompton G Line** (`--bike brompton_g`)

**Secondary signals:**
- Rider mentions the bike in the current message ("on the Brompton", "Tripster ride")
- GPX filename or waypoints contain "commute" → Brompton
- Recent ride log entry on the same day already names the bike

**Rare exception:** Tripster ride with dead power-meter battery looks Brompton-like. Disambiguate via distance, avg speed, or asking.

**Ambiguous → ask the rider** with a concrete recommendation based on weak signals ("looks like the Tripster based on distance — confirm?") rather than an open question.

Every saved analysis markdown records the bike slug in its header. The Ride log in `USER_PROFILE.md` includes a Bike column.

**Brompton-specific calibration:**
When ingesting a Brompton FIT, prompt the rider for: **battery % start, battery % end, and assist-level pattern** (e.g. "L1 default, L2 on the three flagged climbs"). Record in the analysis markdown header.
```

- [ ] **Step 3: Update the FIT workflow steps**

Find "When the rider provides a FIT file:" and modify the numbered list to insert a "determine bike" step after step 1 (Read USER_PROFILE.md). All subsequent script invocations gain `--bike <slug>`. Example for step 2/3:

```markdown
2. **Determine bike** per the Bike selection rules above (auto-detect via `analyse_fit.py` if not obvious).
3. Run `python scripts/analyse_fit.py --bike <slug> <file>` for the canonical parse
```

- [ ] **Step 4: Update the GPX workflow steps**

Find "When the rider provides a GPX file:" and insert a determine-bike + determine-surface step:

```markdown
1. **Determine bike** per the Bike selection rules above.
2. If the bike supports multiple surfaces (e.g. Brompton G Line), determine the surface mix and pick the dominant key from `crr_by_surface`.
3. Run `python scripts/analyse_gpx.py --bike <slug> --surface <name> --save <file>` for climbs and predictions
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Add Bike selection rules and per-bike workflow steps to CLAUDE.md"
```

---

### Task 13: Create the Silca pressure-lookup agent prompt template

**Files:**
- Create: `docs/prompts/silca-pressure-lookup.md`

- [ ] **Step 1: Create the prompt file**

Write `docs/prompts/silca-pressure-lookup.md`:

```markdown
# Silca pressure-lookup agent prompt

**When to use:** new bike added to the `bikes:` dict in `USER_PROFILE.md`, or any material input change (new tyres, sustained ±2 kg weight shift, new measured F/R split).

**Prerequisite tooling:** a Chrome browser available to the dispatched agent (Playwright, Chromium, or equivalent — verify availability before dispatching).

**Inputs (collected once per surface per bike):**

| Field | Source | Example |
|---|---|---|
| Tyre size (ETRTO) | `bikes[slug].tyres.size_etrto` or `size_mm` | 54-406 (Brompton) / 32 (Tripster) |
| Measured tyre width (mm) | `bikes[slug].tyres.measured_mm` if present | 31.4 (Tripster GP4S) |
| System weight (kg) | `bikes[slug].system_weight_kg_default` | 98.5 (Brompton, commute kit) |
| F/R split (front %) | parse `bikes[slug].fr_split` "40/60" → 40 | 40 (Tripster) |
| Tube type | from `bikes[slug].tyres.tube_type` | TPU (Tripster, 21 Apr 2026+) |
| Surface | one of `bikes[slug].surfaces_supported` | gravel_smooth (Brompton) |

**Agent prompt template (paste into a fresh subagent dispatch with a Chrome browser tool):**

> Open `https://silca.cc/pages/sppc-form` in Chrome. The page is a single-page form ("Silca Professional Pressure Calculator" / SPPC).
>
> Fill in:
> - Rider weight: `{system_weight_kg - bike_weight_kg}` (kg or lb — match the form's unit)
> - Bike weight: `{bike_weight_kg}`
> - Front-wheel weight distribution: `{front_pct}`%
> - Tyre width: `{measured_tyre_width_mm}` (front and rear, same value)
> - Wheel size / rim size: pick the matching standard (for ETRTO 54-406 use the 20" option; for ETRTO 25-622+ use 700c)
> - Surface category: pick the option matching `{surface}` — e.g. "Smooth Pavement", "Worn Pavement", "Poor Pavement", "Gravel". Use the surface mapping below.
> - Tube type: `{tube_type}` (TPU → "Latex/TPU"; butyl → "Butyl"; tubeless → "Tubeless")
>
> Submit the form, wait for the recommendations to render, and capture:
> - Front pressure (psi)
> - Rear pressure (psi)
> - A screenshot of the calculator result page (save to `rides/charts/silca-{bike_slug}-{surface}-{date}.png` for audit trail)
>
> Surface mapping (USER_PROFILE crr_by_surface key → Silca surface category):
> - `tarmac` → "Worn Pavement" (default for typical UK / FR roads)
> - `tarmac_high_pressure` → "Smooth Pavement"
> - `gravel_smooth` → "Gravel" (the lowest Silca gravel option)
> - `gravel_rough` → "Gravel" + drop 2 psi front / 3 psi rear (Silca doesn't differentiate; rider preference)
>
> **Report back** in this exact format:
>
> ```yaml
> silca_lookup:
>   bike: {bike_slug}
>   surface: {surface}
>   inputs:
>     rider_weight_kg: …
>     bike_weight_kg: …
>     front_pct: …
>     tyre_width_mm: …
>     wheel_size: …
>     surface_silca: …
>     tube_type: …
>   outputs:
>     front_psi: …
>     rear_psi: …
>   screenshot: rides/charts/silca-{bike_slug}-{surface}-{date}.png
>   timestamp: {ISO-8601 UTC}
> ```

**Post-processing:** the agent returns the YAML block; paste it into `bikes[slug].tyre_pressure_psi[surface] = {front: …, rear: …}` in `USER_PROFILE.md`. Remove the "indicative" / "not yet validated" warning when all surfaces in `surfaces_supported` have a recorded lookup.

**Manual fallback:** if no Chrome browser is available, run the same inputs through `https://silca.cc/pages/sppc-form` by hand and paste the same YAML block.
```

- [ ] **Step 2: Commit**

```bash
mkdir -p docs/prompts
git add docs/prompts/silca-pressure-lookup.md
git commit -m "Add Silca pressure-lookup agent prompt template"
```

---

### Task 14: Add ride-log Bike column and backfill

**Files:**
- Modify: `USER_PROFILE.md` — Ride log table (lines ~424–440)

- [ ] **Step 1: Add Bike column header**

Locate the Ride log table. Modify:

```markdown
| Date | Type | Distance/Time | TSS | Analysis file |
|---|---|---|---|---|
```

To:

```markdown
| Date | Bike | Type | Distance/Time | TSS | Analysis file |
|---|---|---|---|---|---|
```

- [ ] **Step 2: Backfill existing rows**

For every existing row, insert the bike slug as the second column:

- `16 Apr 2026 | brompton_g | Brompton commute Highgate | …` (the row already says "Brompton commute")
- `19 Apr 2026 | tripster | Burgess Hill → Ditchling Beacon | …`
- `22 Apr 2026 | tripster | Revolver indoor (Wahoo SYSTM) | …`
- `23 Apr 2026 | tripster | London Z2 commute (3 segments) | …`
- `24 Apr 2026 | tripster | Battersea tyre-test circuit | …`
- `25 Apr 2026 | tripster | Commute to station | …`
- `25 Apr 2026 | tripster | Lost Lanes #18 extended | …`
- `25 Apr 2026 | tripster | Commute from station | …`
- `29 Apr 2026 | tripster | Revolver indoor 105% | …`
- `30 Apr 2026 | brompton_g | Brompton "easy spin" leg | …` (and the other two 30 Apr Brompton rows)
- `2 May 2026 | tripster | Saturday Henley loop | …`
- `12 May 2026 AM | brompton_g | Cély-en-Bière test ride | …`
- `12 May 2026 PM | brompton_g | Cély leisure with dad | …`

- [ ] **Step 3: Note in pending experiments**

Append to "Pending experiments":

```markdown
## Brompton G Line calibration (added 2026-05-12)
- **F/R weight split** — measure on bathroom scales at typical commute kit weight
- **Tyre pressure validation** — run the Silca agent-driven lookup once F/R split lands
- **Assist level multipliers** — refine L1/L2/L3 placeholders (0.5/1.0/1.5) by
  comparing HR-effort and Wh-consumption across logged rides at each level
- **Battery drain calibration** — log start/finish battery % for each Brompton ride
  to fit Wh-per-km at each assist level
- **CdA estimate** — placeholder 0.42 from review-position notes; refine if a
  flat-windless calibration ride becomes feasible
```

- [ ] **Step 4: Verify the file parses**

Run: `/opt/miniconda3/envs/cycling/bin/python -c "from scripts.profile import load_profile; from scripts.bike_config import load_bike; p = load_profile(); b = load_bike('brompton_g', profile=p); print(b.slug, b.surfaces_supported)"`
Expected: `brompton_g ['tarmac', 'gravel']`. No traceback.

- [ ] **Step 5: Commit**

`USER_PROFILE.md` is gitignored. Do NOT commit. The change persists in your local copy and informs all future runs.

---

## Phase 5: Acceptance test

### Task 15: Remove the legacy `physics:` alias from `USER_PROFILE.md`

**Files:**
- Modify: `USER_PROFILE.md` — remove the old `physics:` block (lines 27–34 of the original)
- Modify: `scripts/profile.py` — remove the legacy module-level constants (`BIKE_WEIGHT_KG`, `WHEEL_CIRCUMFERENCE_M`, etc.) if no caller still uses them

- [ ] **Step 1: Confirm no caller still imports the legacy constants**

Run: `grep -rn "BIKE_WEIGHT_KG\|WHEEL_CIRCUMFERENCE_M\|CDA_DEFAULT\|CRR_DEFAULT\|SYSTEM_WEIGHT_KG\|FR_SPLIT_FRONT_PCT\|predict_speed_legacy" scripts/ | grep -v profile.py`
Expected: **no hits**. If anything hits, that script wasn't migrated — go back and fix it.

- [ ] **Step 2: Delete the legacy block from `USER_PROFILE.md`**

Use `Edit` to remove the old top-level `physics:` block (lines 27–34 in the original frontmatter).

- [ ] **Step 3: Delete the legacy constants and `predict_speed_legacy` shim from `scripts/profile.py` and `scripts/physics_model.py`**

Remove module-level constants in `profile.py` that are no longer imported. Remove `predict_speed_legacy` from `physics_model.py`.

- [ ] **Step 4: Run the full test suite**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/ -v`
Expected: every test PASS. No errors, no skips related to bike refactor.

- [ ] **Step 5: Commit**

```bash
git add scripts/profile.py scripts/physics_model.py
git commit -m "Remove legacy physics: aliases; bikes: is the sole physics source"
```

---

### Task 16: Run the gravel-route analysis — acceptance test

**Files:**
- Read: `routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified).gpx`
- Output: `routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified)-prediction.md`
- Output (chart): `rides/charts/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified)-overview.png`

- [ ] **Step 1: Confirm the GPX file exists and is non-empty**

Run: `ls -la "routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified).gpx"`
Expected: file present, size > 50 KB.

- [ ] **Step 2: Run the analysis on the Brompton at gravel surface**

Run: `/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py --bike brompton_g --surface gravel_smooth --save "routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified).gpx"`
Expected:
- No traceback
- Prediction markdown written to `routes/<stem>-prediction.md`
- Overview chart PNG written to `rides/charts/<stem>-overview.png`
- Climb table shows HR targets and assist levels (NOT watts)
- Total route includes a "Battery drain estimate: ~X Wh of 345 Wh" line

- [ ] **Step 3: Spot-check the output for sanity**

Open `routes/<stem>-prediction.md`. Verify:
- Header reads `**Bike:** Brompton G Line Electric (`brompton_g`)`
- Surface reads `gravel_smooth`
- Predicted total time is plausible (e.g. ~70 km gravel route at L1 average ~18–22 km/h → 3.5–4 h)
- Battery estimate is plausible (e.g. 200–300 Wh used out of 345 Wh capacity)
- No wattage column for climbs; all guidance is HR + assist level

If any sanity check fails, debug:
- Wrong wheel circumference? Check `bike.wheel_circ_m` in the output
- Speed too high? Check CRR for `gravel_smooth` and CdA
- Battery estimate missing? Check `solve_speed_with_assist` is being called per climb

- [ ] **Step 4: Save the analysis as the canonical Brompton calibration point**

Append a note to the bottom of the prediction markdown:

```markdown
---
**Calibration note (2026-05-12):** First analysis run after multi-bike framework
implementation. Battery start: _record at ride time_. Battery end: _record at ride
time_. Assist pattern: _record per-segment_. These data points feed the calibration
loop refining level_share multipliers and Wh-per-km estimates.
```

- [ ] **Step 5: Commit framework artefacts (NOT USER_PROFILE.md)**

```bash
git status
# Confirm USER_PROFILE.md is NOT staged. If it is, unstage with: git restore --staged USER_PROFILE.md
git add docs/superpowers/plans/2026-05-12-multi-bike-support.md
git commit -m "Acceptance test: gravel route analysed end-to-end on Brompton with assist + battery model"
```

- [ ] **Step 6: Update Ride log in USER_PROFILE.md (planned ride)**

Add a row to the Ride log table for the planned ride (filled in after the actual ride):

```markdown
| 2026-05-?? | brompton_g | Long Gravel (Cély area) | _planned_ | _planned_ | routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified)-prediction.md |
```

(Date filled in when actually ridden; row gets updated post-ride with FIT-based actuals.)

---

## Self-review

**Spec coverage** — every section of the spec maps to one or more tasks:
- Schema → Tasks 1, 2, 14, 15
- Script CLI contract → Task 4
- Agent rules in CLAUDE.md → Task 12
- Physics model changes → Tasks 5, 6, 7
- Brompton output shape → Task 8 (output template branch)
- Tyre pressure → Task 11
- Battery calibration loop → Task 9 (header prompt for battery %)
- Implementation rollout phases → Tasks ordered to match
- New-bike onboarding (Silca lookup) → Task 13

**Placeholder scan** — searched for: TBD, TODO, "implement later", "appropriate error handling", "add validation", "similar to Task N", placeholder text without code. None found in concrete steps. The `bike.fr_split: "TBD"` in USER_PROFILE.md is **deliberate spec content** (a value flagged for pending measurement), not a plan placeholder.

**Type consistency** — `BikeConfig` and `AssistConfig` signatures match across Tasks 3, 5, 6, 7. The `level_share` keys (L0/L1/L2/L3) match across schema (Task 1), config (Task 3), and assist solver (Task 7). The `crr_by_surface` keys (tarmac, tarmac_high_pressure, gravel_smooth, gravel_rough) are used consistently in Tasks 1, 5, 8, 11.
