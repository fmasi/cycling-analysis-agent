"""Tests for the profile loader (PyYAML-backed, lazy constants, bike resolution).

Driven by the `synthetic_profile*` fixtures in conftest — never the real
gitignored USER_PROFILE.md.
"""
import profile


def test_fr_split_parse():
    assert profile.parse_fr_split("40/60") == 40.0
    assert profile.parse_fr_split("45/55") == 45.0
    assert profile.parse_fr_split("46.5/53.5") == 46.5
    assert profile.parse_fr_split("40") == 40.0          # bare number
    assert profile.parse_fr_split(None) is None
    assert profile.parse_fr_split("not-a-split") is None


def test_bare_import_is_side_effect_free():
    # Importing the module must not have populated the constants cache; that
    # happens lazily on first constant access / load_profile() call.
    import importlib
    mod = importlib.import_module("profile")
    # Other tests may have triggered a load already, so just assert the
    # mechanism exists and is a dict.
    assert isinstance(mod._CONSTANTS_CACHE, dict)


def test_quote_colon_scalars_quotes_freetext_but_not_numbers():
    import yaml
    body = (
        "fitness:\n"
        "  ftp_w: 171\n"
        "  power_note: 2026-06-19 (post): 60s peak 230 W (vs MAP: 210)\n"
    )
    data = yaml.safe_load(profile._quote_colon_scalars(body))  # must not raise
    assert data["fitness"]["ftp_w"] == 171                    # number stays a number
    assert "230 W" in data["fitness"]["power_note"]           # free-text preserved


def test_quote_colon_scalars_ignores_colon_in_inline_comment():
    import yaml
    # The only colon is inside the ` # ...` comment → value stays numeric.
    body = "a:\n  battery_wh: 345  # Derived: 9.6 Ah x 36 V = 345 Wh\n"
    data = yaml.safe_load(profile._quote_colon_scalars(body))
    assert data["a"]["battery_wh"] == 345        # int, not a quoted string


def test_quote_colon_scalars_preserves_block_flow_and_quoted():
    import yaml
    body = (
        "a:\n"
        "  notes: |\n"
        "    line with: a colon stays literal\n"
        "  flow: [x, y]\n"
        "  ratio: \"1:2\"\n"
    )
    data = yaml.safe_load(profile._quote_colon_scalars(body))
    assert "line with: a colon" in data["a"]["notes"]   # block body untouched
    assert data["a"]["flow"] == ["x", "y"]              # flow list intact
    assert data["a"]["ratio"] == "1:2"                  # already-quoted intact


def test_load_profile_tolerates_unquoted_colons(tmp_path):
    # Regression: a hand-edited profile with ride-log colons must NOT silently
    # fall back to all-defaults (the PyYAML-strictness regression).
    p = tmp_path / "USER_PROFILE.md"
    p.write_text(
        "---\n"
        "fitness:\n"
        "  ftp_w: 250\n"
        "  power_note: 2026-06-19: 60s peak 230 W (vs MAP: 210)\n"
        "training_load:\n"
        "  source: live going in: CTL 39 / ATL 61 / TSB +1.5\n"
        "---\n"
    )
    prof = profile.load_profile(p)
    assert prof["fitness"]["ftp_w"] == 250            # not the 200 default
    assert "230 W" in prof["fitness"]["power_note"]
    assert "CTL 39" in prof["training_load"]["source"]


def test_active_bike_physics_resolution(synthetic_profile):
    phys = synthetic_profile["physics"]
    # default_bike is "roadie" → its scalars fold into physics
    assert phys["system_weight_kg"] == 90.0
    assert phys["fr_split_front_pct"] == 45.0    # "45/55" -> 45.0
    assert phys["bike_weight_kg"] == 8.5
    assert phys["cda"] == 0.30
    assert phys["wheel_circ_m"] == 2.105
    # environment constants the bike doesn't define fall back to DEFAULTS
    assert phys["air_density_kg_m3"] == profile.DEFAULTS["physics"]["air_density_kg_m3"]
    assert phys["crr"] == profile.DEFAULTS["physics"]["crr"]


def test_block_scalar_does_not_leak_sibling_keys(synthetic_profile):
    # The `note: |` block in the fitness section contains "ftp_w: 999"; the
    # PyYAML parser keeps it as the note's text, NOT a sibling ftp_w override.
    assert synthetic_profile["fitness"]["ftp_w"] == 250
    assert "999" in synthetic_profile["fitness"]["note"]


def test_defaults_filled_for_missing_sections(synthetic_profile):
    # training_load isn't in the synthetic profile → comes from DEFAULTS
    assert synthetic_profile["training_load"]["ctl"] == 0.0


def test_power_zone_bounds_uses_loaded_ftp(synthetic_profile_path, monkeypatch):
    # Point the lazy constants at the synthetic profile and clear the cache.
    monkeypatch.setattr(profile, "_find_profile_path", lambda: synthetic_profile_path)
    profile._CONSTANTS_CACHE.clear()
    profile.load_profile.cache_clear()
    try:
        zones = profile.power_zone_bounds()
        names = [z[0] for z in zones]
        assert names[0].startswith("Z1") and names[-1].startswith("Z8")
        # Z5 Threshold upper bound == FTP (250)
        z5 = next(z for z in zones if z[0].startswith("Z5"))
        assert z5[2] == 250
    finally:
        profile._CONSTANTS_CACHE.clear()
        profile.load_profile.cache_clear()


def test_example_profile_parses(synthetic_profile):  # fixture unused; just need a load first
    # Regression guard: the shipped example template has a leading HTML comment
    # before the `---` frontmatter. A fresh clone (no USER_PROFILE.md) falls
    # back to it, so it must parse — not silently degrade to all-defaults.
    example = profile._repo_root() / "USER_PROFILE.example.md"
    prof = profile.load_profile(example)
    assert prof.get("default_bike") == "roadbike"
    assert "roadbike" in (prof.get("bikes") or {})


def test_load_peer_and_list_peers(synthetic_profile_path, monkeypatch):
    monkeypatch.setattr(profile, "_find_profile_path", lambda: synthetic_profile_path)
    profile.load_profile.cache_clear()
    try:
        assert profile.list_peers() == ["alex"]
        alex = profile.load_peer("alex")
        assert alex is not None and alex["ftp_w"] == 240
        assert profile.load_peer("nobody") is None
    finally:
        profile.load_profile.cache_clear()
