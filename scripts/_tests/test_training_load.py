"""Known-answer tests for training_load.py (CTL / ATL / TSB).

Coaching decisions (fatigue, form, taper) rest on these numbers, so the expected
values below are literals worked out by hand from the conventions in CLAUDE.md
("Training load definitions") and the training_load.py docstring, not recomputed
with the module's own code:

- CTL: 42-day EMA of daily TSS, alpha = 1 - exp(-1/42) = 0.0235283133...
- ATL:  7-day EMA of daily TSS, alpha = 1 - exp(-1/7)  = 0.1331221002...
- TSB: lag-1 (TrainingPeaks): the form carried INTO day N is end-of-day N-1
  CTL minus end-of-day N-1 ATL. Never same-day CTL - ATL.

With the exponential alpha, N days of constant TSS T from zero give
CTL = T * (1 - exp(-N/42)) exactly, so 42 days of 100 TSS land on
100 * (1 - 1/e) = 63.2120558829. That closed form anchors several cases.

All inputs are synthetic.
"""

import pytest

import training_load as tl

TOL = 1e-9


# --- the decay constants ------------------------------------------------------

def test_decay_windows_are_42_and_7_days():
    assert tl.CTL_DECAY_DAYS == 42
    assert tl.ATL_DECAY_DAYS == 7


def test_alphas_use_the_exponential_form():
    # 1 - exp(-1/42) and 1 - exp(-1/7), not the simple 1/42 and 1/7.
    assert tl.CTL_K == pytest.approx(0.0235283133477567, abs=1e-15)
    assert tl.ATL_K == pytest.approx(0.1331221002498184, abs=1e-15)
    assert tl.CTL_K != pytest.approx(1 / 42, abs=1e-6)
    assert tl.ATL_K != pytest.approx(1 / 7, abs=1e-6)


# --- project(): shape --------------------------------------------------------

def test_empty_plan_projects_nothing():
    assert tl.project(50.0, 60.0, []) == []


def test_rows_are_index_tss_ctl_atl_tsb():
    rows = tl.project(10.0, 20.0, [30.0, 40.0])
    assert [r[0] for r in rows] == [0, 1]
    assert [r[1] for r in rows] == [30.0, 40.0]
    assert all(len(r) == 5 for r in rows)


# --- project(): known answers --------------------------------------------------

def test_worked_two_day_example():
    # Start: end-of-yesterday CTL 50, ATL 60. Plan: 100 TSS, then a rest day.
    (_, _, ctl0, atl0, tsb0), (_, _, ctl1, atl1, tsb1) = tl.project(50.0, 60.0, [100.0, 0.0])

    assert tsb0 == pytest.approx(-10.0, abs=TOL)               # 50 - 60
    assert ctl0 == pytest.approx(51.1764156674, abs=TOL)       # 50 + 50 * 0.0235283
    assert atl0 == pytest.approx(65.3248840100, abs=TOL)       # 60 + 40 * 0.1331221

    assert tsb1 == pytest.approx(-14.1484683426, abs=TOL)      # ctl0 - atl0
    assert ctl1 == pytest.approx(49.9723209236, abs=TOL)       # ctl0 * exp(-1/42)
    assert atl1 == pytest.approx(56.6286982520, abs=TOL)       # atl0 * exp(-1/7)


def test_single_session_from_zero():
    (_, _, ctl0, atl0, tsb0), (_, _, _, _, tsb1) = tl.project(0.0, 0.0, [100.0, 0.0])
    assert ctl0 == pytest.approx(2.3528313348, abs=TOL)
    assert atl0 == pytest.approx(13.3122100250, abs=TOL)
    assert tsb0 == pytest.approx(0.0, abs=TOL)                 # nothing carried in yet
    assert tsb1 == pytest.approx(-10.9593786902, abs=TOL)      # the session shows up next day


def test_42_days_of_constant_load_from_zero():
    rows = tl.project(0.0, 0.0, [100.0] * 42)
    _, _, ctl, atl, _ = rows[-1]
    assert ctl == pytest.approx(63.2120558829, abs=TOL)        # 100 * (1 - e^-1)
    assert atl == pytest.approx(99.7521247823, abs=TOL)        # 100 * (1 - e^-6)


def test_7_days_of_constant_load_from_zero():
    _, _, _, atl, _ = tl.project(0.0, 0.0, [100.0] * 7)[-1]
    assert atl == pytest.approx(63.2120558829, abs=TOL)        # 100 * (1 - e^-1)


def test_rest_decays_by_one_over_e_per_window():
    _, _, ctl, _, _ = tl.project(100.0, 100.0, [0.0] * 42)[-1]
    assert ctl == pytest.approx(36.7879441171, abs=TOL)        # 100 / e
    _, _, _, atl, _ = tl.project(100.0, 100.0, [0.0] * 7)[-1]
    assert atl == pytest.approx(36.7879441171, abs=TOL)        # 100 / e


def test_steady_state_holds():
    # Daily TSS equal to CTL and ATL keeps both flat and TSB at zero
    # (CLAUDE.md: equilibrium weekly TSS = CTL * 7).
    for _, _, ctl, atl, tsb in tl.project(65.0, 65.0, [65.0] * 14):
        assert ctl == pytest.approx(65.0, abs=TOL)
        assert atl == pytest.approx(65.0, abs=TOL)
        assert tsb == pytest.approx(0.0, abs=TOL)


# --- TSB convention -----------------------------------------------------------

def test_tsb_is_lag_one_not_same_day():
    ctl_start, atl_start = 48.0, 55.0
    plan = [120.0, 0.0, 60.0, 200.0, 30.0]
    rows = tl.project(ctl_start, atl_start, plan)

    # Day 0 carries in the starting state.
    assert rows[0][4] == pytest.approx(ctl_start - atl_start, abs=TOL)
    # Every later day carries in the previous day's end-of-day CTL - ATL.
    for prev, row in zip(rows, rows[1:]):
        assert row[4] == pytest.approx(prev[2] - prev[3], abs=TOL)
    # And it is NOT the same-day value (CLAUDE.md: never same-day CTL - ATL).
    same_day = rows[0][2] - rows[0][3]
    assert rows[0][4] != pytest.approx(same_day, abs=1e-3)


# --- assess_tsb(): the bands in CLAUDE.md "Safe ranges" --------------------------

@pytest.mark.parametrize("tsb, expected", [
    (15.1, "detraining risk (long periods)"),
    (15.0, "fresh / tapering"),
    (5.0, "fresh / tapering"),
    (4.9, "balanced"),
    (0.0, "balanced"),
    (-5.0, "balanced"),
    (-5.1, "mild productive fatigue"),
    (-10.0, "mild productive fatigue"),
    (-10.1, "productive fatigue (good adaptation)"),
    (-20.0, "productive fatigue (good adaptation)"),
    (-20.1, "high fatigue, monitor"),
    (-25.0, "high fatigue, monitor"),
    (-25.1, "OVERREACHING — high illness/injury risk"),
])
def test_assess_tsb_band_edges(tsb, expected):
    assert tl.assess_tsb(tsb) == expected


# --- CLI -----------------------------------------------------------------------

def run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.argv", ["training_load.py", *args])
    tl.main()
    return capsys.readouterr().out


def test_cli_prints_the_worked_example(monkeypatch, capsys):
    out = run_cli(monkeypatch, capsys,
                  "--ctl", "50", "--atl", "60", "--plan", "100,0", "--labels", "Mon,Tue")
    assert "CTL 50.0, ATL 60.0, TSB -10.0" in out
    mon = next(line for line in out.splitlines() if line.startswith("Mon"))
    tue = next(line for line in out.splitlines() if line.startswith("Tue"))
    assert mon.split()[1:5] == ["100", "51.2", "65.3", "-10.0"]
    assert tue.split()[1:5] == ["0", "50.0", "56.6", "-14.1"]
    assert "Total TSS: 100" in out
    assert "<200 TSS in a week" in out


def test_cli_rejects_mismatched_labels(monkeypatch, capsys):
    out = run_cli(monkeypatch, capsys,
                  "--ctl", "50", "--atl", "60", "--plan", "100,0", "--labels", "Mon")
    assert "ERROR: --labels count must match --plan count" in out
    assert "Total TSS" not in out
