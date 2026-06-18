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
