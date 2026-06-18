import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import profile


# A synthetic profile frontmatter body — deliberately NOT the real (gitignored)
# USER_PROFILE.md, so the suite stays stable regardless of the rider's live
# data. Exercises the active-bike resolution helpers that back the module
# constants SYSTEM_WEIGHT_KG / FR_SPLIT_FRONT_PCT.
SAMPLE = """\
default_bike: tripster

bikes:
  tripster:
    name: Kinesis Decade Tripster
    bike_weight_kg: 11.6
    system_weight_kg_default: 92.1  # 80 body + 11.6 bike + 0.5 kit
    fr_split: "40/60"
    cda: 0.28
    drivetrain_efficiency: 0.97
    wheel_circ_m: 2.155
    tyres:
      model: Continental GP 4 Seasons
      size_mm: 32
    crr_by_surface:
      tarmac: 0.0050
    notes: |
      Block scalar with a colon: should not leak into the bike's scalars.
      Pressures below this require TPU tubes.

  brompton_g:
    name: Brompton G Line Electric
    bike_weight_kg: 19.5
    system_weight_kg_default: 100.5
    fr_split: "40/60"
    cda: 0.42
"""


def test_fr_split_parse():
    assert profile._parse_fr_split("40/60") == 40.0
    assert profile._parse_fr_split("48/52") == 48.0
    assert profile._parse_fr_split("46.5/53.5") == 46.5
    assert profile._parse_fr_split("40") == 40.0          # bare number
    assert profile._parse_fr_split(None) is None
    assert profile._parse_fr_split("not-a-split") is None


def test_top_scalar():
    assert profile._parse_top_scalar(SAMPLE, "default_bike") == "tripster"
    assert profile._parse_top_scalar(SAMPLE, "missing") is None


def test_parse_bikes_captures_immediate_scalars_only():
    bikes = profile._parse_bikes(SAMPLE)
    assert set(bikes) == {"tripster", "brompton_g"}
    trip = bikes["tripster"]
    # immediate scalar children captured (inline comment stripped)
    assert trip["system_weight_kg_default"] == 92.1
    assert trip["fr_split"] == "40/60"
    assert trip["bike_weight_kg"] == 11.6
    assert trip["cda"] == 0.28
    # nested sub-blocks must NOT leak in as bike-level scalars
    assert "model" not in trip       # under tyres:
    assert "tarmac" not in trip      # under crr_by_surface:
    # the two bikes stay separate (no flat-parser cross-contamination)
    assert bikes["brompton_g"]["system_weight_kg_default"] == 100.5


def test_bike_to_physics_default_bike_resolution():
    bikes = profile._parse_bikes(SAMPLE)
    phys = profile._bike_to_physics(
        bikes["tripster"], profile.DEFAULTS["physics"], "tripster"
    )
    assert phys["system_weight_kg"] == 92.1
    assert phys["fr_split_front_pct"] == 40.0    # "40/60" -> 40.0
    assert phys["bike_weight_kg"] == 11.6
    assert phys["cda"] == 0.28
    # a key the bike doesn't define falls back to the generic default
    assert phys["crr"] == profile.DEFAULTS["physics"]["crr"]


def test_missing_keys_fall_back_to_defaults():
    # A bike missing both essentials keeps defaults (and warns to stderr).
    phys = profile._bike_to_physics({}, profile.DEFAULTS["physics"], "bare")
    assert phys["system_weight_kg"] == profile.DEFAULTS["physics"]["system_weight_kg"]
    assert phys["fr_split_front_pct"] == profile.DEFAULTS["physics"]["fr_split_front_pct"]
