"""Tests for rouvy_cleanup: telling ROUVY's upload from HealthFit's Apple Health copy (synthetic records)."""
import rouvy_cleanup as rc

ROUVY = {"id": "i100", "start_date_local": "2030-01-07T15:31:44", "type": "VirtualRide",
         "name": "ROUVY - Tempo 3x8 - 2030-01-07", "device_name": "VIRTUALTRAINING Rouvy",
         "external_id": "rouvy-ext-1", "moving_time": 3448, "elapsed_time": 3538,
         "icu_average_watts": 120}
COPY = {"id": "i200", "start_date_local": "2030-01-07T15:31:44", "type": "VirtualRide",
        "name": "Indoor Cycling", "device_name": "iPhone",
        "external_id": "2030-01-07-153144-Indoor Cycling-ROUVY.fit", "moving_time": 3443, "icu_average_watts": None}
SPIN = {"id": "i3", "start_date_local": "2030-01-06T18:00:00", "type": "VirtualRide", "name": "Indoor Cycling",
        "device_name": "Watch", "external_id": "2030-01-06-180000-Indoor Cycling-Watch.fit",
        "icu_average_watts": None}


def test_classifies_rouvy_copy_and_leaves_watch_spin_alone():
    assert rc.is_rouvy(ROUVY) and not rc.is_healthfit_copy(ROUVY)
    assert rc.is_healthfit_copy(COPY) and not rc.is_rouvy(COPY)
    assert not rc.is_rouvy(SPIN) and not rc.is_healthfit_copy(SPIN)


def test_pairs_only_same_ride():
    assert rc.icu_copies([ROUVY, COPY, SPIN]) == [(ROUVY, COPY)]
    later = dict(COPY, start_date_local="2030-01-07T18:31:44")
    assert rc.icu_copies([ROUVY, later]) == []
    assert rc.icu_copies([COPY]) == []          # no ROUVY upload yet: leave the copy alone


def test_card_title_and_upload_name():
    assert rc.card_title(ROUVY) == "Tempo 3x8"
    assert rc.upload_name(ROUVY) == "2030-01-07-rouvy-i100.fit"


def test_tp_cards():
    hf = {"device_files": [{"file_name": "2030-01-07-153144-Indoor-Cycling-ROUVY.fit.gz"}],
          "metrics": {"duration_actual": 0.957, "avg_power": None, "normalized_power": None}}
    ours = {"device_files": [{"file_name": "2030-01-07-rouvy-tempo.fit.gz"}],
            "metrics": {"duration_actual": 0.9628, "avg_power": 120.0, "normalized_power": 124.0}}
    merged = {"device_files": [hf["device_files"][0], ours["device_files"][0]],
              "metrics": {"duration_actual": 1.94, "avg_power": 60.0, "normalized_power": 124.0}}
    assert rc.tp_is_healthfit_copy(hf) and not rc.tp_is_healthfit_copy(ours) and not rc.tp_is_healthfit_copy(merged)
    assert rc.tp_card_has_rouvy(ours, ROUVY) and not rc.tp_card_has_rouvy(hf, ROUVY)
    assert not rc.tp_card_has_rouvy(merged, ROUVY)      # doubled duration: not a clean ROUVY card
    named = {"device_files": [{"file_name": "2030-01-07-rouvy-i100.fit.gz"}], "metrics": {}}
    assert rc.tp_card_has_rouvy(named, ROUVY)


class FakeResp:
    def __init__(self, content=b"FIT"):
        self.content = content

    def raise_for_status(self):
        pass


class FakeICU:
    def __init__(self, acts):
        self.acts, self.calls = acts, []

    def activities(self, oldest, newest, fields=None):
        return self.acts

    def _req(self, method, path, athlete_scoped=True, **kw):
        self.calls.append((method, path))
        return FakeResp()


def test_clean_icu_deletes_only_the_copy_and_only_with_apply():
    icu = FakeICU([ROUVY, COPY, SPIN])
    assert rc.clean_icu(icu, "2030-01-07", apply=False) == [ROUVY] and icu.calls == []
    rc.clean_icu(icu, "2030-01-07", apply=True)
    assert icu.calls == [("DELETE", "/activity/i200")]


def fake_tp(monkeypatch, cards, upload_error=False):
    """Stand-in tp_mcp.tools modules; returns the call log."""
    import sys
    import types
    log = []

    async def get_workouts(start, end, workout_filter="all"):
        return {"workouts": [{"id": c["id"], "sport": "Bike"} for c in cards]}

    async def get_workout(wid):
        return next(c for c in cards if str(c["id"]) == wid)

    async def create(day, sport, title, **kw):
        log.append(("create", title))
        return {"workout_id": 999}

    async def upload(wid, file_path=None, workout_day=None):
        log.append(("upload", wid, file_path.rsplit("/", 1)[-1]))
        return {"isError": True, "message": "422"} if upload_error else {"message": "ok"}

    async def delete(wid):
        log.append(("delete", wid))
        return {"success": True}

    workouts = types.SimpleNamespace(tp_get_workouts=get_workouts, tp_get_workout=get_workout,
                                     tp_create_workout=create, tp_delete_workout=delete)
    files = types.SimpleNamespace(tp_upload_workout_file=upload)
    monkeypatch.setitem(sys.modules, "tp_mcp", types.ModuleType("tp_mcp"))
    monkeypatch.setitem(sys.modules, "tp_mcp.tools", types.ModuleType("tp_mcp.tools"))
    monkeypatch.setitem(sys.modules, "tp_mcp.tools.workouts", workouts)
    monkeypatch.setitem(sys.modules, "tp_mcp.tools.workout_files", files)
    return log


HF_CARD = {"id": 7, "title": "Indoor Cycling", "metrics": {"duration_actual": 0.957, "avg_power": None},
           "device_files": [{"file_name": "2030-01-07-153144-Indoor-Cycling-ROUVY.fit.gz"}]}


def test_clean_tp_uploads_to_a_fresh_card_before_deleting_the_copy(monkeypatch):
    import asyncio
    log = fake_tp(monkeypatch, [HF_CARD])
    assert asyncio.run(rc.clean_tp(FakeICU([]), "2030-01-07", [ROUVY], apply=True)) == 0
    assert log == [("create", "Tempo 3x8"), ("upload", "999", "2030-01-07-rouvy-i100.fit"), ("delete", "7")]


def test_clean_tp_keeps_the_copy_when_the_upload_fails(monkeypatch):
    import asyncio
    log = fake_tp(monkeypatch, [HF_CARD], upload_error=True)
    assert asyncio.run(rc.clean_tp(FakeICU([]), "2030-01-07", [ROUVY], apply=True)) == 1
    assert ("delete", "7") not in log


def test_clean_tp_dry_run_and_already_clean(monkeypatch):
    import asyncio
    log = fake_tp(monkeypatch, [HF_CARD])
    asyncio.run(rc.clean_tp(FakeICU([]), "2030-01-07", [ROUVY], apply=False))
    assert log == []
    ours = {"id": 8, "title": "Tempo 3x8", "metrics": {"duration_actual": 0.96, "normalized_power": 124.0,
                                                       "avg_power": 120.0},
            "device_files": [{"file_name": "2030-01-07-rouvy-i100.fit.gz"}]}
    log = fake_tp(monkeypatch, [ours])
    assert asyncio.run(rc.clean_tp(FakeICU([]), "2030-01-07", [ROUVY], apply=True)) == 0 and log == []
