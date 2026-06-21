import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elevation_fallback import GPXZClient


def test_no_key_means_unconfigured(tmp_path):
    c = GPXZClient(key_path=tmp_path / "missing.key")
    assert c.configured is False


def test_with_key_is_configured(tmp_path):
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key\n")
    c = GPXZClient(key_path=k)
    assert c.configured is True


def test_unconfigured_raises_on_sample(tmp_path):
    c = GPXZClient(key_path=tmp_path / "missing.key")
    import pytest
    with pytest.raises(RuntimeError):
        c.sample_polyline([(51.5, -0.1)])


def test_sample_polyline_batches_and_returns_floats(tmp_path):
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key")
    c = GPXZClient(key_path=k)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "results": [{"elevation": 100.0}, {"elevation": 110.0}]
    }

    with patch("elevation_fallback.requests.post", return_value=fake_resp) as post:
        out = c.sample_polyline([(51.5, -0.1), (51.6, -0.1)])

    assert out == [100.0, 110.0]
    assert post.call_count == 1


def test_length_mismatch_raises(tmp_path):
    # API returns fewer elevations than points → refuse to misalign coords.
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key")
    c = GPXZClient(key_path=k)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"results": [{"elevation": 100.0}]}  # 1 for 2

    import pytest
    with patch("elevation_fallback.requests.post", return_value=fake_resp):
        with pytest.raises(RuntimeError):
            c.sample_polyline([(51.5, -0.1), (51.6, -0.1)])


def test_sample_polyline_chunks_over_512(tmp_path):
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key")
    c = GPXZClient(key_path=k)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "results": [{"elevation": 1.0}] * 512
    }

    coords = [(51.5, -0.1)] * 1024
    with patch("elevation_fallback.requests.post", return_value=fake_resp) as post, \
         patch("elevation_fallback.time.sleep"):
        out = c.sample_polyline(coords)

    assert len(out) == 1024
    assert post.call_count == 2
