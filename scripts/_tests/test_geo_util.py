"""Tests for shared geo helpers."""
import pytest

from geo_util import haversine_m, bbox_from_gpx


def test_haversine_one_degree_latitude_is_about_111km():
    d = haversine_m(51.0, 0.0, 52.0, 0.0)
    assert 110_000 < d < 112_000


def test_haversine_zero_distance():
    assert haversine_m(51.5, -0.1, 51.5, -0.1) == 0.0


def test_bbox_from_gpx(tmp_path):
    gpx = tmp_path / "r.gpx"
    gpx.write_text(
        '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        '<trkpt lat="51.0" lon="-0.2"/><trkpt lat="51.4" lon="0.1"/>'
        '</trkseg></trk></gpx>'
    )
    assert bbox_from_gpx(gpx) == (-0.2, 51.0, 0.1, 51.4)


def test_bbox_empty_raises(tmp_path):
    gpx = tmp_path / "empty.gpx"
    gpx.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"></gpx>')
    with pytest.raises(ValueError):
        bbox_from_gpx(gpx)
