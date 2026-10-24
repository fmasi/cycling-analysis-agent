"""Tests for icu_tp_wellness: TP metric rows -> intervals.icu wellness fields, never overwriting."""
import icu_tp_wellness as tw


def row(date, label, value, source="", time=""):
    return {"date": date, "label": label, "value": value, "source": source, "time": time, "type": ""}


def test_weight_prefers_the_first_withings_reading():
    rows = [row("2026-10-24", "Weight", "83.9", "Withings", "2026-10-24T18:00:00"),
            row("2026-10-24", "Weight", "83.77", "Withings", "2026-10-24T07:49:00"),
            row("2026-10-24", "Weight", "84.5", "Garmin Health", "2026-10-24T06:00:00"),
            row("2026-10-25", "Weight", "84.1", "Garmin Health", "2026-10-25T06:00:00")]
    d = tw.day_values(rows)
    assert d["2026-10-24"]["weight"] == 83.77 and d["2026-10-25"]["weight"] == 84.1


def test_field_mapping_and_comments():
    rows = [row("2025-01-02", "Sleep Hours", "7.5", "Garmin Health"),
            row("2025-01-02", "Pulse", "47", "Garmin Health"),
            row("2025-01-02", "Pulse", "85", "Withings"),            # blood-pressure pulse: skipped
            row("2025-01-02", "HRV", "73", "Garmin Health"),
            row("2025-01-02", "Blood Pressure", "[129, 85]", "Withings"),
            row("2025-01-02", "Percent Fat", "20.06", "Withings"),
            row("2025-01-02", "Injury", "5"),
            row("2025-01-02", "Body Battery", "[24, 92, 53]", "Garmin Health")]
    f = tw.day_values(rows)["2025-01-02"]
    assert f["sleepSecs"] == 27000 and f["restingHR"] == 47 and f["hrv"] == 73.0
    assert (f["systolic"], f["diastolic"]) == (129, 85) and f["bodyFat"] == 20.1
    assert "TP injury 5/10" in f["comments"] and "Body Battery avg 53" in f["comments"]


def test_existing_values_are_never_overwritten():
    new = {"2026-10-24": {"weight": 83.77, "hrv": 60.0}, "2026-10-23": {"weight": 83.0}}
    existing = {"2026-10-24": {"weight": 83.5, "hrv": None}, "2026-10-23": {"weight": 82.9}}
    assert tw.missing_only(new, existing) == [{"id": "2026-10-24", "hrv": 60.0}]
