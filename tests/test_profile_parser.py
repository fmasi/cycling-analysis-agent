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
