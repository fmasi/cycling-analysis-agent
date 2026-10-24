"""Tests for icu_upload_history's duplicate planner and readers (hermetic: no network, no fitparse)."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import icu_upload_history as up

T0 = datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)


def cand(name, start_min, dur_min, sport="cycling", power=False):
    return up.Candidate(path=Path(name), start=T0 + timedelta(minutes=start_min), duration_s=dur_min * 60,
                        sport=sport, device=up.device_of(name), has_power=power)


def actions(cands):
    return {c.path.name: (c.action, c.reason) for c in cands}


def test_device_of():
    assert up.device_of("2026-10-22-141403-ELEMNT_ROAM_66CD-178-0.fit.gz") == "wahoo"
    assert up.device_of("X.fmasi.2026-10-19.TPAppleHealth.fit.gz") == "applewatch"
    assert up.device_of("fmasi-2015-06-06-8-minute-test.tcx.gz") == "other"


def test_watch_copy_of_a_roam_ride_is_dropped_for_the_power_file():
    roam = cand("ELEMNT_ROAM.fit", 0, 60, power=True)
    watch = cand("TPAppleHealth.fit", 1, 61)
    up.plan([roam, watch], [], set(), None)
    assert roam.action == "upload"
    assert watch.action == "skip" and watch.keeper is roam


def test_split_ride_keeps_both_sequential_parts():
    # ROAM profile switch: 3-min file, then the rest of the ride 2 min after the first started
    a, b = cand("ELEMNT_a.fit", 0, 3, power=True), cand("ELEMNT_b.fit", 4, 90, power=True)
    up.plan([a, b], [], set(), None)
    assert a.action == b.action == "upload"


def test_different_activities_at_the_same_time_are_not_duplicates():
    ride = cand("ELEMNT_ROAM.fit", 0, 60, power=True)
    tennis = cand("TPAppleHealth.fit", 0, 60, sport="tennis")
    up.plan([ride, tennis], [], set(), None)
    assert ride.action == tennis.action == "upload"


def test_no_chaining_through_a_duplicate():
    # A and C don't overlap each other; B overlaps both. B is worst, so A and C must both survive.
    a = cand("A_garmin.fit", 0, 60, power=True)
    b = cand("B.TPAppleHealth.fit", 30, 60)
    c = cand("C_garmin.fit", 61, 60, power=True)
    up.plan([a, b, c], [], set(), None)
    assert (a.action, c.action) == ("upload", "upload") and b.action == "skip"


def test_existing_activity_and_log_and_deferral():
    already = cand("garmin_x.fit", 0, 60, power=True)
    logged = cand("garmin_y.fit", 200, 30, sport="running")
    ride_2026 = cand("garmin_z.fit", 400, 60, power=True)
    walk_2026 = cand("TPAppleHealth_w.fit", 600, 30, sport="walking")
    existing = [(T0 + timedelta(minutes=2), T0 + timedelta(minutes=58), "ride")]
    up.plan([already, logged, ride_2026, walk_2026], existing, {str(logged.path)}, date(2026, 1, 1))
    got = actions([already, logged, ride_2026, walk_2026])
    assert got["garmin_x.fit"] == ("skip", "already in intervals.icu")
    assert got["garmin_y.fit"] == ("skip", "already uploaded (log)")
    assert got["garmin_z.fit"][0] == "defer"            # a ride in the Wahoo-sync era
    assert got["TPAppleHealth_w.fit"][0] == "upload"    # a walk can't be in the Wahoo backfill


def test_existing_activity_of_another_kind_does_not_block():
    walk = cand("TPAppleHealth.fit", 0, 30, sport="walking")
    up.plan([walk], [(T0, T0 + timedelta(minutes=30), "ride")], set(), None)
    assert walk.action == "upload"


def test_keeper_prefers_power_then_device_then_length():
    g = cand("garmin.fit", 0, 50, power=True)
    w = cand("ELEMNT.fit", 0, 40, power=False)
    assert sorted([w, g], key=up.keeper_order)[0] is g
    w2 = cand("ELEMNT.fit", 0, 40, power=True)
    assert sorted([g, w2], key=up.keeper_order)[0] is w2


def test_read_tcx_and_gpx():
    tcx = (b'<TrainingCenterDatabase><Activities><Activity Sport="Biking"><Id>x</Id>'
           b'<Lap StartTime="2015-06-06T10:00:00Z"><TotalTimeSeconds>600.0</TotalTimeSeconds></Lap>'
           b'<Lap StartTime="2015-06-06T10:10:00Z"><TotalTimeSeconds>300.5</TotalTimeSeconds>'
           b'<Trackpoint><Extensions><ns3:Watts>210</ns3:Watts></Extensions></Trackpoint></Lap>'
           b'</Activity></Activities></TrainingCenterDatabase>')
    start, dur, sport, power = up.read_tcx(tcx)
    assert start == datetime(2015, 6, 6, 10, tzinfo=timezone.utc) and dur == 900.5
    assert sport == "Biking" and power is True
    gpx = b'<gpx><trk><trkseg><trkpt><time>2026-03-22T09:00:00Z</time></trkpt><trkpt><time>2026-03-22T09:45:00Z</time></trkpt></trkseg></trk></gpx>'
    start, dur, sport, power = up.read_gpx(gpx)
    assert dur == 2700 and sport == "gpx" and not power


def test_sport_family():
    assert up.sport_family("cycling") == up.sport_family("Ride") == up.sport_family("Biking") == "ride"
    assert up.sport_family("running") == up.sport_family("VirtualRun") == "run"
    assert up.sport_family("tennis") == up.sport_family("53") == "other"
