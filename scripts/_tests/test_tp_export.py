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


def test_is_completed():
    assert tx.is_completed({"completed": True})
    assert tx.is_completed({"totalTime": 1.5})
    assert not tx.is_completed({"completed": False, "totalTime": None})


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
    ex = tx.Exporter(client, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex, ["workouts", "files"]) == 0
    target = tmp_path / "rides/fit/tp-export/2026/2026-10-20_1_11_ride.fit.gz"
    assert gzip.decompress(target.read_bytes()) == b"fit-1"
    with (tmp_path / "rides/fit/tp-export/manifest.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [(r["workout_id"], r["file_id"]) for r in rows] == [("1", "11")]
    assert rows[0]["sha256"] == tx.payload_sha256(b"fit-1")
    # the planned-only card never triggers a details call
    assert not any(e.endswith("/workouts/2/details") for _, e in client.calls)

    # Second run reuses the cached cards, details and file: no new downloads.
    client2 = FakeClient(_routes(raw_calls))
    ex2 = tx.Exporter(client2, tmp_path, date(2026, 10, 1), date(2026, 10, 24), retry_delay=0)
    assert run(ex2, ["files"]) == 0
    assert len(raw_calls) == 1
    assert ex2.stats["files_cached"] == 1 and ex2.stats["details_cached"] == 1


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
