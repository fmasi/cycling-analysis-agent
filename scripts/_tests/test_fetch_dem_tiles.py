import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch_dem_tiles import (
    bbox_from_gpx,
    os_grid_tiles_for_bbox,
    ign_tiles_for_bbox,
    dept_for_bbox,
    asc_keys_for_bbox,
    _asc_filename_matches,
    resolve_ign_archive_url,
    fetch_uk_tile,
    fetch_tiles,
)


def test_bbox_from_gpx(tmp_path):
    gpx = tmp_path / "t.gpx"
    gpx.write_text("""<?xml version='1.0'?>
<gpx version='1.1' xmlns='http://www.topografix.com/GPX/1/1'>
<trk><trkseg>
<trkpt lat='51.0' lon='0.0'/><trkpt lat='51.1' lon='0.2'/>
</trkseg></trk></gpx>""")
    bbox = bbox_from_gpx(gpx)
    assert bbox == (0.0, 51.0, 0.2, 51.1)


def test_os_grid_tiles_includes_TQ_for_kent():
    tiles = os_grid_tiles_for_bbox((0.0, 51.1, 0.5, 51.3))
    assert any(t.startswith("TQ") for t in tiles)


# ---------------- UK ----------------

def test_fetch_uk_tile_skips_when_present(tmp_path):
    out_dir = tmp_path / "uk-1m" / "TQ45"
    out_dir.mkdir(parents=True)
    (out_dir / "TQ45ne.tif").write_bytes(b"x" * 1024)
    with patch("fetch_dem_tiles._uk_resolve_product_guid") as guid, \
         patch("fetch_dem_tiles._stream_download") as dl:
        result = fetch_uk_tile("TQ45", tmp_path)
    guid.assert_not_called()
    dl.assert_not_called()
    assert result["skipped"] is True


# ---------------- FR ----------------

def test_dept_for_bbox_fontainebleau():
    # A generic point inside D077 (Seine-et-Marne, ~Fontainebleau Forest area).
    bbox = (2.40, 48.40, 2.55, 48.50)
    assert "D077" in dept_for_bbox(bbox)


def test_ign_tiles_for_bbox_returns_dept_codes():
    out = ign_tiles_for_bbox((2.40, 48.40, 2.55, 48.50))
    assert "D077" in out


def test_asc_keys_for_bbox_fontainebleau():
    bbox = (2.49, 48.45, 2.51, 48.47)  # generic D077 sample bbox
    keys = asc_keys_for_bbox(bbox)
    # This bbox is at Lambert-93 E~663, N~6817 (km).
    assert any(660 <= e <= 665 and 6815 <= n <= 6820 for e, n in keys)


def test_asc_filename_matches_correct_pattern():
    wanted = {(660, 6850), (661, 6850)}
    assert _asc_filename_matches(
        "RGEALTI_FXX_0660_6850_MNT_LAMB93_IGN69.asc", wanted
    )
    assert _asc_filename_matches(
        "some/path/RGEALTI_FXX_0661_6850_MNT_LAMB93_IGN69.asc", wanted
    )
    assert not _asc_filename_matches(
        "RGEALTI_FXX_0699_9999_MNT_LAMB93_IGN69.asc", wanted
    )
    assert not _asc_filename_matches("README.txt", wanted)


def test_resolve_ign_archive_url_finds_match_in_atom_feed():
    fake_atom = """<?xml version="1.0"?>
<feed>
<entry><title>RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D077_2021-03-03</title></entry>
</feed>"""
    mock_resp = MagicMock(status_code=200, text=fake_atom)
    mock_resp.raise_for_status = lambda: None
    with patch("fetch_dem_tiles.requests.get", return_value=mock_resp):
        url = resolve_ign_archive_url("D077")
    assert url.endswith(
        "RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D077_2021-03-03/"
        "RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D077_2021-03-03.7z"
    )


def test_resolve_ign_archive_url_picks_newest_date():
    fake_atom = """<feed>
<entry><title>RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D077_2019-01-01</title></entry>
<entry><title>RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D077_2024-06-15</title></entry>
<entry><title>RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D077_2021-03-03</title></entry>
</feed>"""
    mock_resp = MagicMock(status_code=200, text=fake_atom)
    mock_resp.raise_for_status = lambda: None
    with patch("fetch_dem_tiles.requests.get", return_value=mock_resp):
        url = resolve_ign_archive_url("D077")
    assert "2024-06-15" in url


def test_fetch_tiles_fr_requires_bbox(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        fetch_tiles(["D077"], region="fr", dest_root=tmp_path)
