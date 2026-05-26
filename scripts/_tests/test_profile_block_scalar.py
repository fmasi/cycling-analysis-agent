"""
Tests for block-scalar (`key: |` and `key: >`) handling in _parse_simple_yaml.

All tests are hermetic — they feed synthetic YAML strings directly to
_parse_simple_yaml (or load_profile via a tmp_path markdown file) and assert on
the resulting dict. No USER_PROFILE.md involvement.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profile import _parse_simple_yaml, load_profile


# ---------------------------------------------------------------------------
# 1. Basic literal block scalar (`|`)
# ---------------------------------------------------------------------------

def test_literal_block_value_captured():
    """The value of a `key: |` field should be a string containing the lines,
    not the bare `|` indicator."""
    yaml_text = """\
name: foo
notes: |
  line one
  line two
count: 3
"""
    p = _parse_simple_yaml(yaml_text)
    assert isinstance(p["notes"], str), "notes should be a string, not '|'"
    assert "line one" in p["notes"]
    assert "line two" in p["notes"]


def test_literal_block_lines_not_sibling_keys():
    """Continuation lines of a block scalar must NOT appear as top-level sibling
    keys in the parsed dict."""
    yaml_text = """\
name: foo
notes: |
  - bullet line
  Some plain text line
count: 3
"""
    p = _parse_simple_yaml(yaml_text)
    # Continuation lines must not leak as keys
    for key in p:
        assert not key.startswith("- "), f"Leaked continuation line as key: {key!r}"
        assert "plain text" not in key.lower(), f"Leaked continuation line as key: {key!r}"


# ---------------------------------------------------------------------------
# 2. Colon-containing continuation line must not leak as a key
# ---------------------------------------------------------------------------

def test_continuation_line_with_colon_not_a_key():
    """A continuation line like `Source: some text` must be absorbed into the
    block scalar value, not parsed as a sibling key `Source`."""
    yaml_text = """\
top:
  field: |
    - Single cutoff applies to all modes
    Source: Brompton G Line Electric Safety Instructions PDF (doc 105465-00)
  sibling: 99
"""
    p = _parse_simple_yaml(yaml_text)
    top = p["top"]
    # "Source" must NOT be a key at the same level as "field"
    assert "Source" not in top, (
        f"Continuation line 'Source: ...' leaked as sibling key. top keys: {list(top.keys())}"
    )
    # "sibling" must still be present and correct
    assert top.get("sibling") == 99


# ---------------------------------------------------------------------------
# 3. Sibling key after block scalar parses correctly
# ---------------------------------------------------------------------------

def test_sibling_after_block_scalar_parses():
    """The key immediately after a block scalar (dedented to the mapping level)
    must be parsed as a normal key, not swallowed."""
    yaml_text = """\
name: foo
notes: |
  line one
  line two
count: 3
"""
    p = _parse_simple_yaml(yaml_text)
    assert "count" in p, "count key missing after block scalar"
    assert p["count"] == 3


# ---------------------------------------------------------------------------
# 4. Nested mapping mirroring real bikes.brompton_g.assist shape
# ---------------------------------------------------------------------------

def test_nested_block_scalar_mirror_real_shape():
    """Mirror the exact assist block shape from USER_PROFILE.md:
      - mapping with name, notes (block scalar with a `- ` line), count
      - asserts keys are exactly {name, notes, count}
      - notes contains both continuation lines
      - count is an int
    """
    yaml_text = """\
name: Brompton G Line
notes: |
  - Single cutoff applies to all modes
  Brompton G Line Electric Safety Instructions PDF (doc 105465-00)
count: 3
"""
    p = _parse_simple_yaml(yaml_text)
    assert set(p.keys()) == {"name", "notes", "count"}, (
        f"Expected keys {{name, notes, count}}, got {set(p.keys())}"
    )
    assert "Single cutoff" in p["notes"]
    assert "Brompton G Line Electric" in p["notes"]
    assert p["count"] == 3


# ---------------------------------------------------------------------------
# 5. Deeply nested block scalar (bikes > brompton_g > assist > assist_levels_source)
# ---------------------------------------------------------------------------

def test_deeply_nested_block_scalar():
    """Block scalar inside a 3-deep nesting must not leak continuation lines
    into the containing assist dict or any ancestor."""
    yaml_text = """\
bikes:
  brompton_g:
    assist:
      cutoff_kph: 25
      assist_levels_source: |
        - Single cutoff applies to all modes
        Source: Brompton G Line Electric Safety Instructions PDF (doc 105465-00)
      next_field: 42
    name: Brompton G Line
"""
    p = _parse_simple_yaml(yaml_text)
    assist = p["bikes"]["brompton_g"]["assist"]
    # "Source" must NOT be a key in assist
    assert "Source" not in assist, (
        f"'Source' leaked as key in assist. assist keys: {list(assist.keys())}"
    )
    # next_field must still parse correctly
    assert assist.get("next_field") == 42
    # assist_levels_source must be a string, not '|'
    src = assist.get("assist_levels_source", "")
    assert isinstance(src, str) and src != "|", (
        f"assist_levels_source should be a string, got {src!r}"
    )
    assert "Single cutoff" in src


# ---------------------------------------------------------------------------
# 6. Block scalar with chomping indicator `|-` (strip trailing newlines)
# ---------------------------------------------------------------------------

def test_block_scalar_with_chomping_indicator():
    """key: |- and key: |+ are valid block scalar indicators and must be handled."""
    yaml_text = """\
notes: |-
  stripped line one
  stripped line two
after: done
"""
    p = _parse_simple_yaml(yaml_text)
    assert isinstance(p["notes"], str) and p["notes"] != "|-", (
        f"notes should be a string (not '|-'), got {p['notes']!r}"
    )
    assert "stripped line" in p["notes"]
    assert p.get("after") == "done"


# ---------------------------------------------------------------------------
# 7. Normal (non-block) scalars are unaffected
# ---------------------------------------------------------------------------

def test_normal_scalar_unaffected():
    """Ensure normal key: value scalars still work correctly after the fix."""
    yaml_text = """\
a: 1
b: 2.5
c: true
d: hello world
"""
    p = _parse_simple_yaml(yaml_text)
    assert p["a"] == 1
    assert p["b"] == 2.5
    assert p["c"] is True
    assert p["d"] == "hello world"


# ---------------------------------------------------------------------------
# 8. load_profile integration: block scalar in frontmatter of a markdown file
# ---------------------------------------------------------------------------

def test_load_profile_with_block_scalar(tmp_path, monkeypatch):
    """load_profile with a markdown file whose frontmatter contains a block
    scalar must not leak continuation lines as top-level or section-level keys."""
    md_content = """\
---
identity:
  name: Test Rider
  location: London
notes: |
  - bullet item
  Source: some reference doc
fitness:
  ftp_w: 250
---

# Profile body
"""
    profile_path = tmp_path / "USER_PROFILE.md"
    profile_path.write_text(md_content, encoding="utf-8")

    # Monkey-patch _find_profile_path to return our tmp file
    import profile as profile_mod
    monkeypatch.setattr(profile_mod, "_find_profile_path", lambda: profile_path)

    p = profile_mod.load_profile()

    # "Source" must not appear as a top-level key
    assert "Source" not in p, f"'Source' leaked as top-level key: {list(p.keys())}"
    # fitness.ftp_w must parse correctly
    assert p["fitness"]["ftp_w"] == 250
