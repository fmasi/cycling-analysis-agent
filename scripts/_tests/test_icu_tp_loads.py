"""Tests for icu_tp_loads: TSS targets per activity, duplicate folding, manual and strength entries."""
import json
from datetime import date

import icu_tp_loads as tl

CARDS = {
    "1": {"workoutId": 1, "workoutDay": "2025-06-01T00:00:00", "tssActual": 80.4, "title": "Ride"},
    "2": {"workoutId": 2, "workoutDay": "2025-06-01T00:00:00", "tssActual": 30.0, "title": "Watch copy"},
    "3": {"workoutId": 3, "workoutDay": "2025-06-02T00:00:00", "tssActual": 45.0, "title": "Gym",
          "workoutTypeValueId": 9, "startTime": "2025-06-02T18:00:00", "totalTime": 1.0},
    "4": {"workoutId": 4, "workoutDay": "2025-06-03T00:00:00", "tssActual": 0, "title": "Planned"},
}
PLAN = [
    {"action": "upload", "path": "rides/fit/tp-export/2025/a_ROAM.fit.gz", "keeper_path": "", "workout_id": "1",
     "file_id": "11", "start_utc": "2025-06-01T08:00:00+00:00", "duration_s": "3600", "sport": "cycling"},
    {"action": "skip", "path": "rides/fit/tp-export/2025/b_TPAppleHealth.fit.gz", "keeper_path": "a_ROAM.fit.gz",
     "workout_id": "2", "file_id": "22", "start_utc": "2025-06-01T08:01:00+00:00", "duration_s": "3500",
     "sport": "cycling"},
]


def test_group_tss_folds_the_dropped_duplicate_into_its_keeper():
    assert tl.group_tss(PLAN, CARDS) == {"a_ROAM.fit.gz": 110.4}


def test_target_by_external_id_and_by_start_time():
    acts = [{"id": "i1", "external_id": "tp:1:11", "start_date": "2025-06-01T08:00:00Z", "type": "Ride"},
            {"id": "i2", "start_date": "2025-06-01T08:02:00Z", "elapsed_time": 3500, "type": "Ride"},
            {"id": "i3", "start_date": "2025-06-01T08:02:00Z", "elapsed_time": 3500, "type": "Walk"}]
    t = tl.target_loads(acts, PLAN, CARDS)
    assert t["i1"] == (110, "external_id")
    assert t["i2"] == (110, "start-time match")      # e.g. the Wahoo-synced copy of the same ride
    assert "i3" not in t                              # a walk at the same time is not the ride


def test_manual_entries_only_for_cards_with_tss_and_no_file():
    m = tl.manual_entries(CARDS, PLAN, date(2025, 1, 1), date(2025, 12, 31))
    assert [e["external_id"] for e in m] == ["tp-manual:3"]
    assert m[0]["type"] == "WeightTraining" and m[0]["icu_training_load"] == 45 and m[0]["moving_time"] == 3600


def test_strength_entries_and_matching(tmp_path):
    p = tmp_path / "strength" / "2026"
    p.mkdir(parents=True)
    (p / "77.json").write_text(json.dumps({"listing": {"id": 77, "prescribedDate": "2026-10-04T00:00:00"},
        "detail": {"id": 77, "title": "Strength", "completedTss": 16.0, "completedTssSource": "HeartRateTss",
                   "startDateTime": "2026-10-04T17:08:00", "executedDurationInSeconds": 1600}}))
    entries = tl.strength_entries(tmp_path, date(2026, 1, 1), date(2026, 10, 25))
    assert entries == [{"external_id": "tp-strength:77", "start_date_local": "2026-10-04T17:08:00",
                        "type": "WeightTraining", "name": "Strength", "moving_time": 1600, "icu_training_load": 16,
                        "description": "Imported from TrainingPeaks strength builder; load = TP TSS (HeartRateTss)."}]
    # no strength activity in intervals.icu yet -> create a manual one
    create, assign = tl.split_strength(entries, [{"id": "r", "type": "Ride", "start_date_local": "2026-10-04T17:10:00"}])
    assert len(create) == 1 and assign == []
    # the Watch session already arrived (HealthFit) -> give it the TSS instead of duplicating
    watch = {"id": "w", "type": "WeightTraining", "start_date_local": "2026-10-04T17:09:00", "elapsed_time": 1500}
    create, assign = tl.split_strength(entries, [watch])
    assert create == [] and assign == [(watch, 16)]
