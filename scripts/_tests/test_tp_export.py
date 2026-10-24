"""Tests for tp_export: windowing, flattening, file-name safety, resumable downloads.

Hermetic: a fake client stands in for tp_mcp's TPClient, so tp-mcp isn't needed.
"""
import asyncio
import csv
import gzip
import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import tp_export as tx


class Code(Enum):
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"


@dataclass
class Resp:
    success: bool = True
    data: object = None
    content: bytes = b""
    error_code: Code | None = None
    message: str = ""


@dataclass
class FakeClient:
    """Routes endpoint suffixes to canned responses and records every call."""
    routes: dict
    calls: list = field(default_factory=list)

    async def ensure_athlete_id(self):
        return 42

    def _route(self, method, endpoint):
        self.calls.append((method, endpoint))
        for suffix, resp in self.routes.items():
            if endpoint.endswith(suffix):
                return resp() if callable(resp) else resp
        return Resp(data=[])

    async def get(self, endpoint, **kw):
        return self._route("get", endpoint)

    async def post(self, endpoint, **kw):
        return self._route("post", endpoint)

    async def get_raw(self, endpoint, **kw):
        return self._route("get_raw", endpoint)


def run(exporter, phases):
    return asyncio.run(exporter.run(phases))


def test_date_windows_cover_range_without_gaps_or_overlap():
    wins = tx.date_windows(date(2015, 6, 1), date(2017, 6, 1))
    assert wins[0][0] == date(2015, 6, 1) and wins[-1][1] == date(2017, 6, 1)
    for (_, e), (s, _) in zip(wins, wins[1:]):
        assert (s - e).days == 1                       # contiguous
    assert all((e - s).days + 1 <= 365 for s, e in wins)
    # 2015-06-01..2017-06-01 inclusive is 732 days (2016 is a leap year) -> 365 + 365 + 2
    assert [(e - s).days + 1 for s, e in wins] == [365, 365, 2]
    assert tx.date_windows(date(2020, 1, 2), date(2020, 1, 1)) == []


def test_flatten_pmc_keeps_tp_lag1_tsb():
    rows = [{"workoutDay": "2026-10-24T00:00:00", "tssActual": None, "tssPlanned": 50.0,
             "ctl": 21.07, "atl": 21.53, "tsb": -3.54}]
    out = tx.flatten_pmc(rows)
    # TSB is passed through as TP reports it (lag-1), not recomputed as same-day CTL-ATL (-0.46).
    assert out == [{"date": "2026-10-24", "tss_actual": 0.0, "tss_planned": 50.0,
                    "ctl": 21.07, "atl": 21.53, "tsb": -3.54}]


def test_flatten_metrics_one_row_per_value():
    entries = [{"timeStamp": "2026-10-24T00:00:00", "details": [
        {"type": 9, "label": "Weight", "value": 83.766, "uploadClient": "Withings", "time": "2026-10-24T10:49:00"},
        {"type": 30, "label": "Injury", "value": 8, "uploadClient": None, "time": "2026-10-24T08:00:00"}]}]
    rows = tx.flatten_metrics(entries)
    assert [(r["label"], r["value"]) for r in rows] == [("Weight", 83.766), ("Injury", 8)]
    assert rows[0]["date"] == "2026-10-24"


def test_safe_name_strips_paths_and_odd_characters():
    assert tx.safe_name("../../etc/passwd") == "passwd"
    assert tx.safe_name("..\\..\\x y.fit.gz") == "x_y.fit.gz"
    assert tx.safe_name(".hidden") == "hidden"
    assert tx.safe_name("") == "file"


def test_file_target_stays_inside_root(tmp_path):
    card = {"workoutId": 7, "workoutDay": "2026-10-23T00:00:00"}
    target = tx.file_target(tmp_path, card, {"fileId": 9, "fileName": "../../../evil.fit"})
    assert target == tmp_path / "2026" / "2026-10-23_7_9_evil.fit"


def test_payload_sha256_sees_through_gzip():
    raw = b"FIT-bytes"
    assert tx.payload_sha256(gzip.compress(raw)) == tx.payload_sha256(raw)


def test_has_session():
    assert tx.has_session({"completed": True})
    assert tx.has_session({"totalTime": 1.5})
    assert tx.has_session({"totalTime": None, "distance": 12000.0})   # distance-only upload
    assert not tx.has_session({"completed": False, "totalTime": None, "tssPlanned": 60})


def _routes(raw_calls):
    cards = [
        {"workoutId": 1, "workoutDay": "2026-10-20T00:00:00", "totalTime": 1.0},
        {"workoutId": 2, "workoutDay": "2026-10-22T00:00:00", "totalTime": None},  # planned only
    ]

    def raw():
        raw_calls.append(1)
        return Resp(content=gzip.compress(b"fit-1"))

    return {
        "/workouts/2026-10-01/2026-10-24": Resp(data=cards),
        "/workouts/1/details": Resp(data={"workoutId": 1, "workoutDeviceFileInfos": [
            {"fileId": 11, "fileName": "ride.fit.gz"}]}),
        "/rawfiledata/11": raw,
    }


def test_files_phase_downloads_once_and_resumes(tmp_path):
    raw_calls = []
    client = FakeClient(_routes(raw_calls))
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), refresh_days=0, retry_delay=0)
    assert run(ex, ["workouts", "files"]) == 0
    target = tmp_path / "rides/fit/tp-export/2026/2026-10-20_1_11_ride.fit.gz"
    assert gzip.decompress(target.read_bytes()) == b"fit-1"
    with (tmp_path / "rides/fit/tp-export/manifest.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [(r["kind"], r["workout_id"], r["file_id"]) for r in rows] == [("device", "1", "11")]
    assert rows[0]["sha256"] == tx.payload_sha256(b"fit-1")
    # the planned-only card never triggers a details call
    assert not any(e.endswith("/workouts/2/details") for _, e in client.calls)

    # Second run reuses the cached cards, details and file: no new downloads.
    client2 = FakeClient(_routes(raw_calls))
    ex2 = tx.Exporter(client2, tmp_path, date(2026, 10, 1), date(2026, 10, 24), refresh_days=0, retry_delay=0)
    assert run(ex2, ["files"]) == 0
    assert len(raw_calls) == 1
    assert ex2.stats["device_cached"] == 1 and ex2.stats["details_cached"] == 1


def test_fitness_is_one_call_over_the_whole_range(tmp_path):
    rows = [{"workoutDay": f"2015-06-0{d}T00:00:00", "tssActual": 10.0, "ctl": 1.0, "atl": 2.0, "tsb": 0.0}
            for d in range(1, 4)]
    client = FakeClient({"/performancedata/2015-06-01/2026-10-24": Resp(data=rows)})
    ex = tx.Exporter(client, tmp_path, date(2015, 6, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["fitness"]) == 0
    assert [m for m, _ in client.calls] == ["post"]          # one call, not one per window
    with (tmp_path / "notes/tp-archive/fitness/pmc-daily.csv").open() as f:
        assert len(list(csv.DictReader(f))) == 3


def test_failures_are_retried_then_reported(tmp_path):
    attempts = []

    def limited():
        attempts.append(1)
        return Resp(success=False, error_code=Code.RATE_LIMITED, message="429")

    client = FakeClient({"/consolidatedtimedmetrics/2026-10-01/2026-10-24": limited})
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["metrics"]) == 1
    assert len(attempts) == 4 and len(ex.failures) == 1
    assert not (tmp_path / "body-comp/tp-metrics-history.csv").exists()


def test_extras_archive_only_own_libraries(tmp_path):
    client = FakeClient({
        "/exerciselibrary/v2/libraries": Resp(data=[
            {"exerciseLibraryId": 5, "ownerId": 42, "libraryName": "Mine"},
            {"exerciseLibraryId": 6, "ownerId": 1, "libraryName": "TP default content"}]),
        "/libraries/5/items": Resp(data=[{"itemName": "Z2 60"}]),
    })
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["extras"]) == 0
    libs = json.loads((tmp_path / "notes/tp-archive/extras/libraries.json").read_text())
    assert libs[0]["items"] == [{"itemName": "Z2 60"}]
    assert "items" not in libs[1]
    assert not any(e.endswith("/libraries/6/items") for _, e in client.calls)


def test_parse_args_rejects_unknown_phase(capsys):
    import pytest
    with pytest.raises(SystemExit):
        tx.parse_args(["--phases", "fitness,bogus"])
    assert "bogus" in capsys.readouterr().err


def test_file_target_rejects_non_numeric_ids(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        tx.file_target(tmp_path, {"workoutId": "../../x", "workoutDay": "2026-10-23"}, {"fileId": 1})
    # a malformed day falls back to a placeholder instead of becoming a path component
    t = tx.file_target(tmp_path, {"workoutId": 3, "workoutDay": "../z"}, {"fileId": -5, "fileName": "a.fit"})
    assert t == tmp_path / "0000" / "0000-00-00_3_-5_a.fit"


def test_narrow_run_merges_instead_of_truncating(tmp_path):
    def pmc(days):
        return Resp(data=[{"workoutDay": f"{d}T00:00:00", "tssActual": 1.0, "ctl": 1.0, "atl": 1.0, "tsb": 0.0}
                          for d in days])
    full = FakeClient({"/performancedata/2026-01-01/2026-10-24": pmc(["2026-01-01", "2026-10-23"]),
                       "/workouts/2026-01-01/2026-10-24": Resp(data=[
                           {"workoutId": 1, "workoutDay": "2026-01-05T00:00:00"}])})
    assert run(tx.Exporter(full, tmp_path, date(2026, 1, 1), date(2026, 10, 24), retry_delay=0),
               ["fitness", "workouts"]) == 0
    # a top-up over the last two days updates 23 Oct, adds 24 Oct and keeps 1 Jan
    top = FakeClient({"/performancedata/2026-10-23/2026-10-24": Resp(data=[
                          {"workoutDay": "2026-10-23T00:00:00", "tssActual": 9.0, "ctl": 2.0, "atl": 3.0, "tsb": 0.5},
                          {"workoutDay": "2026-10-24T00:00:00", "tssActual": 0.0, "ctl": 2.0, "atl": 2.5, "tsb": -1.0}]),
                      "/workouts/2026-10-23/2026-10-24": Resp(data=[
                          {"workoutId": 2, "workoutDay": "2026-10-24T00:00:00"}])})
    assert run(tx.Exporter(top, tmp_path, date(2026, 10, 23), date(2026, 10, 24), retry_delay=0),
               ["fitness", "workouts"]) == 0
    rows = json.loads((tmp_path / "notes/tp-archive/fitness/performancedata.json").read_text())
    assert [(r["workoutDay"][:10], r["tssActual"]) for r in rows] == [
        ("2026-01-01", 1.0), ("2026-10-23", 9.0), ("2026-10-24", 0.0)]
    cards = json.loads((tmp_path / "notes/tp-archive/workouts/2026.json").read_text())
    assert [c["workoutId"] for c in cards] == [1, 2]


def test_two_device_files_and_attachment(tmp_path):
    card = {"workoutId": 1, "workoutDay": "2026-10-20T00:00:00", "totalTime": 1.0}
    client = FakeClient({
        "/workouts/2026-10-01/2026-10-24": Resp(data=[card]),
        "/workouts/1/details": Resp(data={"workoutDeviceFileInfos": [
            {"fileId": 11, "fileName": "roam.fit.gz"}, {"fileId": 12, "fileName": "watch.fit.gz"}],
            "attachmentFileInfos": [{"fileId": 13, "fileName": "photo.jpg"}]}),
        "/rawfiledata/11": Resp(content=b"a"), "/rawfiledata/12": Resp(content=b"b"),
        "/rawfiledata/13": Resp(content=b"jpg"),
    })
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["workouts", "files"]) == 0
    assert ex.stats["device_downloaded"] == 2 and ex.stats["attachment_downloaded"] == 1
    assert (tmp_path / "notes/tp-archive/attachments/2026/2026-10-20_1_13_photo.jpg").read_bytes() == b"jpg"


def test_missing_file_is_redownloaded_and_truncated_details_refetched(tmp_path):
    raw_calls = []
    ex = tx.Exporter(FakeClient(_routes(raw_calls)), tmp_path, date(2026, 10, 1), date(2026, 10, 24),
                     refresh_days=0, retry_delay=0)
    run(ex, ["workouts", "files"])
    (tmp_path / "rides/fit/tp-export/2026/2026-10-20_1_11_ride.fit.gz").unlink()
    (tmp_path / "notes/tp-archive/details/2026/1.json").write_text('{"workoutDevice')   # truncated
    ex2 = tx.Exporter(FakeClient(_routes(raw_calls)), tmp_path, date(2026, 10, 1), date(2026, 10, 24),
                      refresh_days=0, retry_delay=0)
    assert run(ex2, ["files"]) == 0
    assert len(raw_calls) == 2 and ex2.stats["details_fetched"] == 1


def test_recent_details_are_refreshed(tmp_path):
    raw_calls = []
    for _ in range(2):
        ex = tx.Exporter(FakeClient(_routes(raw_calls)), tmp_path, date(2026, 10, 1), date(2026, 10, 24),
                         refresh_days=14, retry_delay=0)
        run(ex, ["workouts", "files"])
    assert ex.stats["details_fetched"] == 1          # 20 Oct is inside the 14-day refresh window
    assert len(raw_calls) == 1                       # ...but the file itself is already on disk


def test_non_retryable_error_is_tried_once(tmp_path):
    attempts = []

    def missing():
        attempts.append(1)
        return Resp(success=False, error_code=Code.NOT_FOUND, message="404")

    client = FakeClient({"/consolidatedtimedmetrics/2026-10-01/2026-10-24": missing})
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["metrics"]) == 1 and len(attempts) == 1


def test_empty_file_body_is_a_failure(tmp_path):
    routes = _routes([])
    routes["/rawfiledata/11"] = Resp(content=b"")
    ex = tx.Exporter(FakeClient(routes), tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["workouts", "files"]) == 1
    assert any("empty file body" in f for f in ex.failures)


def test_auth_failure_stops_the_run(tmp_path):
    class AuthCode(Enum):
        AUTH_EXPIRED = "AUTH_EXPIRED"

    client = FakeClient({"/workouts/2026-10-01/2026-10-24": Resp(
        success=False, error_code=AuthCode.AUTH_EXPIRED, message="401")})
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["workouts", "files", "extras"]) == 2
    assert len(client.calls) == 1                    # nothing after the first auth error


def test_files_phase_without_cards_fails(tmp_path):
    ex = tx.Exporter(FakeClient({}), tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["files"]) == 1


def test_limit_takes_most_recent_sessions(tmp_path):
    cards = [{"workoutId": i, "workoutDay": f"2026-10-{10 + i:02d}T00:00:00", "totalTime": 1.0} for i in range(1, 4)]
    client = FakeClient({"/workouts/2026-10-01/2026-10-24": Resp(data=cards)})
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), limit=1, retry_delay=0)
    run(ex, ["workouts", "files"])
    detail_calls = [e for _, e in client.calls if e.endswith("/details")]
    assert detail_calls == ["/fitness/v6/athletes/42/workouts/3/details"]


def test_cached_cards_respect_the_window(tmp_path):
    tx.write_json(tmp_path / "notes/tp-archive/workouts/2026.json", [
        {"workoutId": 1, "workoutDay": "2026-09-30T00:00:00"},
        {"workoutId": 2, "workoutDay": "2026-10-02T00:00:00"}])
    ex = tx.Exporter(FakeClient({}), tmp_path, date(2026, 10, 1), date(2026, 10, 24))
    assert [c["workoutId"] for c in ex._cached_cards()] == [2]


def test_limit_rejects_zero():
    import pytest
    with pytest.raises(SystemExit):
        tx.parse_args(["--limit", "0"])


def test_old_format_single_object_archive_is_replaced_not_misread(tmp_path):
    tx.write_json(tmp_path / "notes/tp-archive/extras/atp.json", {"weeks": [1, 2]})   # pre-merge format
    client = FakeClient({"/atp/2026-10-01/2026-10-24": Resp(data=[{"id": 1, "week": "2026-10-19"}])})
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    run(ex, ["extras"])
    assert json.loads((tmp_path / "notes/tp-archive/extras/atp.json").read_text()) == [{"id": 1, "week": "2026-10-19"}]
