"""Tests for rouvy_cleanup: telling ROUVY's upload from HealthFit's Apple Health copy (shapes from 2026-10-26)."""
import rouvy_cleanup as rc

ROUVY = {"id": "i201957068", "start_date_local": "2026-10-26T15:31:44", "type": "VirtualRide",
         "name": "ROUVY - Tempo endurance 3x8 (Rouvy test) - 2026-10-26", "device_name": "VIRTUALTRAINING Rouvy",
         "external_id": "261026163043-mc37tbij", "moving_time": 3448, "elapsed_time": 3538,
         "icu_average_watts": 120}
COPY = {"id": "i201970964", "start_date_local": "2026-10-26T15:31:44", "type": "VirtualRide",
        "name": "Indoor Cycling", "device_name": "iPhone17,2",
        "external_id": "2026-10-26-153144-Indoor Cycling-ROUVY.fit", "moving_time": 3443, "icu_average_watts": None}
SPIN = {"id": "i3", "start_date_local": "2026-10-25T18:00:00", "type": "VirtualRide", "name": "Indoor Cycling",
        "device_name": "Watch7,5", "external_id": "2026-10-25-180000-Indoor Cycling-Watch.fit",
        "icu_average_watts": None}


def test_classifies_rouvy_copy_and_leaves_watch_spin_alone():
    assert rc.is_rouvy(ROUVY) and not rc.is_healthfit_copy(ROUVY)
    assert rc.is_healthfit_copy(COPY) and not rc.is_rouvy(COPY)
    assert not rc.is_rouvy(SPIN) and not rc.is_healthfit_copy(SPIN)


def test_pairs_only_same_ride():
    assert rc.icu_copies([ROUVY, COPY, SPIN]) == [(ROUVY, COPY)]
    later = dict(COPY, start_date_local="2026-10-26T18:31:44")
    assert rc.icu_copies([ROUVY, later]) == []
    assert rc.icu_copies([COPY]) == []          # no ROUVY upload yet: leave the copy alone


def test_card_title_and_upload_name():
    assert rc.card_title(ROUVY) == "Tempo endurance 3x8 (Rouvy test)"
    assert rc.upload_name(ROUVY) == "2026-10-26-rouvy-i201957068.fit"


def test_tp_cards():
    hf = {"device_files": [{"file_name": "2026-10-26-153144-Indoor-Cycling-ROUVY.fit.gz"}],
          "metrics": {"duration_actual": 0.957, "avg_power": None, "normalized_power": None}}
    ours = {"device_files": [{"file_name": "2026-10-26-rouvy-tempo-3x8.fit.gz"}],
            "metrics": {"duration_actual": 0.9628, "avg_power": 120.0, "normalized_power": 124.0}}
    merged = {"device_files": [hf["device_files"][0], ours["device_files"][0]],
              "metrics": {"duration_actual": 1.94, "avg_power": 60.0, "normalized_power": 124.0}}
    assert rc.tp_is_healthfit_copy(hf) and not rc.tp_is_healthfit_copy(ours) and not rc.tp_is_healthfit_copy(merged)
    assert rc.tp_card_has_rouvy(ours, ROUVY) and not rc.tp_card_has_rouvy(hf, ROUVY)
    assert not rc.tp_card_has_rouvy(merged, ROUVY)      # doubled duration: not a clean ROUVY card
    named = {"device_files": [{"file_name": "2026-10-26-rouvy-i201957068.fit.gz"}], "metrics": {}}
    assert rc.tp_card_has_rouvy(named, ROUVY)
