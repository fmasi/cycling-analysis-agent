"""Tests for icu_push_plan: plan entries -> intervals.icu events, and the read-back check."""
import icu_push_plan as pp


def test_workout_event_fields_and_stable_external_id():
    ev = pp.to_event({"date": "2026-10-31", "type": "Ride", "name": "Long Z2 1h30 (HR)", "load": 63,
                      "minutes": 90, "target": "HR", "description": "- 75m 69-83% LTHR"})
    assert ev == {"category": "WORKOUT", "start_date_local": "2026-10-31T00:00:00", "name": "Long Z2 1h30 (HR)",
                  "description": "- 75m 69-83% LTHR", "external_id": "plan:2026-10-31:long-z2-1h30-hr",
                  "type": "Ride", "icu_training_load": 63, "moving_time": 5400, "target": "HR"}


def test_note_has_no_workout_fields():
    ev = pp.to_event({"date": "2026-11-01", "category": "NOTE", "name": "Rest"})
    assert ev["category"] == "NOTE" and "type" not in ev and "icu_training_load" not in ev


def test_check_flags_unparsed_steps_and_push_errors():
    ev = pp.to_event({"date": "2026-10-31", "type": "Ride", "name": "x", "description": "Main\n- 75m 69-83% LTHR"})
    assert pp.check(ev, {"workout_doc": {"steps": []}}) == ["structured steps in the description were not parsed"]
    assert pp.check(ev, {"workout_doc": {"steps": [1]}, "push_errors": ["WAHOO: 403"]}) == ["push errors: ['WAHOO: 403']"]
    strength = pp.to_event({"date": "2026-10-27", "type": "WeightTraining", "name": "S", "description": "1 Leg press 3x10"})
    assert pp.check(strength, {}) == []
