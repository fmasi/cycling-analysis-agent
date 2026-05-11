import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from map_match import _subsample, _cache_key, match_coords


def test_subsample_keeps_endpoints_under_cap():
    coords = [(51.0 + i * 0.001, 0.0) for i in range(100)]
    out = _subsample(coords, max_n=10)
    assert len(out) <= 10
    assert out[0] == coords[0]
    assert out[-1] == coords[-1]


def test_subsample_returns_input_when_short():
    coords = [(51.0, 0.0), (51.001, 0.0), (51.002, 0.0)]
    assert _subsample(coords, max_n=10) == coords


def test_cache_key_deterministic_per_coords():
    a = [(51.123456, 0.000001), (51.123457, 0.000002)]
    b = [(51.123456, 0.000001), (51.123457, 0.000002)]
    c = [(51.123456, 0.000001), (51.999999, 0.000002)]
    assert _cache_key(a) == _cache_key(b)
    assert _cache_key(a) != _cache_key(c)


def test_match_coords_falls_back_on_http_error(tmp_path):
    coords = [(51.0, 0.0), (51.001, 0.001), (51.002, 0.002)]
    mock_resp = MagicMock(status_code=500)
    with patch("map_match.requests.get", return_value=mock_resp):
        out = match_coords(coords, cache_dir=tmp_path)
    assert out == coords  # unchanged on error


def test_match_coords_falls_back_on_network_exception(tmp_path):
    coords = [(51.0, 0.0), (51.001, 0.001)]
    with patch("map_match.requests.get", side_effect=Exception("dns fail")):
        out = match_coords(coords, cache_dir=tmp_path)
    assert out == coords


def test_match_coords_returns_snapped_geometry(tmp_path):
    coords = [(51.0, 0.0), (51.001, 0.001), (51.002, 0.002)]
    fake_response = {
        "code": "Ok",
        "matchings": [{
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [0.0001, 51.0001],
                    [0.0010, 51.0010],
                    [0.0020, 51.0020],
                ],
            },
            "confidence": 0.99,
        }],
    }
    mock_resp = MagicMock(status_code=200)
    mock_resp.json = MagicMock(return_value=fake_response)
    with patch("map_match.requests.get", return_value=mock_resp):
        out = match_coords(coords, cache_dir=tmp_path)
    assert len(out) == 3
    # GeoJSON is [lon, lat]; we return (lat, lon)
    assert out[0] == (51.0001, 0.0001)
    assert out[-1] == (51.0020, 0.0020)


def test_match_coords_cache_hits_on_second_call(tmp_path):
    coords = [(51.0, 0.0), (51.001, 0.001)]
    fake_response = {
        "code": "Ok",
        "matchings": [{"geometry": {"coordinates": [[0.0, 51.0], [0.001, 51.001]]}}],
    }
    mock_resp = MagicMock(status_code=200)
    mock_resp.json = MagicMock(return_value=fake_response)
    with patch("map_match.requests.get", return_value=mock_resp) as mock_get:
        match_coords(coords, cache_dir=tmp_path)
        match_coords(coords, cache_dir=tmp_path)
    assert mock_get.call_count == 1  # second call hit cache
