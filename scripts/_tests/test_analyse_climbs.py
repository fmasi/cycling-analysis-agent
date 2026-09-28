"""analyse_climbs.write_markdown: the climb report.

Regression: REFERENCE_CLIMB_INDEX was deleted in 6a4da74 (#17) while
write_markdown still used it, so every ride with a climb crashed with a
NameError before its report was written. Synthetic climb only.
"""

import analyse_climbs


def test_reference_benchmark_is_high_cat_3():
    # 1.45 km at 9 % (module docstring): index 13.05, inside Cat 3's 6-16 band.
    assert round(analyse_climbs.REFERENCE_CLIMB_INDEX, 2) == 13.05
    name, *_ = analyse_climbs.categorise(1.45, 9.0)
    assert name == "Cat 3"


def test_report_with_a_climb_is_written(tmp_path, monkeypatch):
    # climb_stats needs a parsed FIT; the per-climb table isn't under test here.
    monkeypatch.setattr(analyse_climbs, "climb_stats", lambda *a, **k: None)
    climb = {
        "start_km": 10.0, "end_km": 11.45, "length_m": 1450.0,
        "gain_m": 130.5, "avg_grad_pct": 9.0, "max_grad_pct": 12.0,
    }
    out = tmp_path / "synthetic-climbs.md"

    analyse_climbs.write_markdown([climb], arrays=None, fit_path="synthetic.fit",
                                  chart_paths=[], out_path=out)

    text = out.read_text()
    assert "| 1 | km 10.00 | 1450 m | 130 m | 9.0% | 12.0% | 13.05 | **Cat 3** |" in text
    assert "index **13.05** (high **Cat 3**)" in text
    assert "had index **13.05** — **100%** of the reference Cat 3" in text


def test_report_without_climbs(tmp_path):
    out = tmp_path / "flat-climbs.md"
    analyse_climbs.write_markdown([], arrays=None, fit_path="flat.fit",
                                  chart_paths=[], out_path=out)
    assert "No categorised climbs detected." in out.read_text()
