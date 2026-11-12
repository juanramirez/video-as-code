"""takeout.py: media is indexed straight from Takeout ZIPs and read back on demand."""
from __future__ import annotations

import datetime as dt
import json
import warnings
import zipfile
from pathlib import Path

import pytest

from video_as_code import takeout
from video_as_code.takeout import LocalTakeoutAsset, TakeoutError, scan_zip_folder


def kind(path: Path) -> str:
    return {".jpg": "image", ".mp4": "video"}.get(path.suffix.lower(), "unknown")


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings(), zipfile.ZipFile(path, "w") as archive:
        warnings.simplefilter("ignore")  # a Takeout may repeat a member name
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def taken(timestamp: dt.datetime) -> bytes:
    return json.dumps({"photoTakenTime": {"timestamp": str(int(timestamp.replace(tzinfo=dt.timezone.utc).timestamp()))}}).encode()


# --- scanning ---------------------------------------------------------------

def test_scan_indexes_only_supported_media(tmp_path):
    make_zip(tmp_path / "takeout-001.zip", {
        "Takeout/Google Photos/2018/a.jpg": b"jpeg",
        "Takeout/Google Photos/2018/a.jpg.json": b"{}",
        "Takeout/Google Photos/2018/b.mp4": b"video",
        "Takeout/Google Photos/2018/notes.txt": b"text",
        "Takeout/Google Photos/2018/empty.jpg": b"",
        "Takeout/Google Photos/2018/": b"",
    })
    assets = scan_zip_folder(tmp_path, kind)
    assert [(a.display_name, a.kind, a.size) for a in assets] == [
        ("takeout-001.zip:Takeout/Google Photos/2018/a.jpg", "image", 4),
        ("takeout-001.zip:Takeout/Google Photos/2018/b.mp4", "video", 5),
    ]


def test_scan_skips_unsafe_member_paths(tmp_path):
    make_zip(tmp_path / "t.zip", {"../evil.jpg": b"x", "/abs.jpg": b"x", "ok/fine.jpg": b"x"})
    assert [a.member for a in scan_zip_folder(tmp_path, kind)] == ["ok/fine.jpg"]


def test_scan_keeps_takeout_paths_for_copies_and_keys(tmp_path):
    make_zip(tmp_path / "part" / "takeout-002.zip", {"Google Photos/2019/c.jpg": b"x"})
    (asset,) = scan_zip_folder(tmp_path, kind)
    assert asset.output_relative == Path("0001_takeout-002", "Google Photos", "2019", "c.jpg")
    assert asset.key.startswith("zip_") and asset.key.endswith("/00000001/Google Photos/2019/c.jpg")


def test_scan_numbers_archives_in_order_and_keys_differ_per_archive(tmp_path):
    make_zip(tmp_path / "a.zip", {"same.jpg": b"1"})
    make_zip(tmp_path / "b.zip", {"same.jpg": b"2"})
    first, second = scan_zip_folder(tmp_path, kind)
    assert first.output_relative.parts[0] == "0001_a" and second.output_relative.parts[0] == "0002_b"
    assert first.key != second.key


def test_scan_repeated_member_name_gets_its_own_folder(tmp_path):
    make_zip(tmp_path / "t.zip", {})
    with warnings.catch_warnings(), zipfile.ZipFile(tmp_path / "t.zip", "w") as archive:
        warnings.simplefilter("ignore")
        archive.writestr("d/x.jpg", b"one")
        archive.writestr("d/x.jpg", b"two")
    one, two = scan_zip_folder(tmp_path, kind)
    assert one.output_relative == Path("0001_t", "d", "x.jpg")
    assert two.output_relative == Path("0001_t", "__item_00000002", "d", "x.jpg")


def test_scan_with_explicit_archives_ignores_the_rest_of_the_folder(tmp_path):
    keep = make_zip(tmp_path / "keep.zip", {"k.jpg": b"x"})
    make_zip(tmp_path / "other.zip", {"o.jpg": b"x"})
    assert [a.member for a in scan_zip_folder(tmp_path, kind, archive_paths=[keep])] == ["k.jpg"]


def test_scan_without_zips_says_so(tmp_path):
    with pytest.raises(TakeoutError, match="No Takeout ZIP"):
        scan_zip_folder(tmp_path, kind)


def test_scan_unreadable_archive_is_a_takeout_error(tmp_path):
    (tmp_path / "broken.zip").write_bytes(b"this is not a zip")
    with pytest.raises(TakeoutError, match="broken.zip"):
        scan_zip_folder(tmp_path, kind)


# --- capture dates ----------------------------------------------------------

def dates(tmp_path, members, **prefer):
    make_zip(tmp_path / "t.zip", members)
    return {Path(a.member).name: a.taken_date for a in scan_zip_folder(tmp_path, kind, **prefer)}


def test_date_from_the_json_sidecar(tmp_path):
    when = dt.datetime(2018, 8, 16, 23, 26)
    got = dates(tmp_path, {"a.jpg": b"x", "a.jpg.json": taken(when)}, prefer_from=dt.date(2018, 1, 1))
    assert got == {"a.jpg": dt.date(2018, 8, 16)}


def test_date_from_the_supplemental_metadata_and_stem_sidecars(tmp_path):
    got = dates(tmp_path, {
        "a.jpg": b"x", "a.jpg.supplemental-metadata.json": taken(dt.datetime(2017, 6, 25, 12)),
        "b.mp4": b"x", "b.json": taken(dt.datetime(2017, 7, 6, 12)),
    }, prefer_to=dt.date(2020, 1, 1))
    assert got == {"a.jpg": dt.date(2017, 6, 25), "b.mp4": dt.date(2017, 7, 6)}


def test_date_falls_back_to_the_file_name(tmp_path):
    got = dates(tmp_path, {"IMG_20170527_203519.jpg": b"x", "VID-2017-05-04-WA0007.mp4": b"x", "plain.jpg": b"x"})
    assert got == {"IMG_20170527_203519.jpg": dt.date(2017, 5, 27),
                   "VID-2017-05-04-WA0007.mp4": dt.date(2017, 5, 4), "plain.jpg": None}


def test_date_in_the_file_name_must_be_a_real_date(tmp_path):
    assert dates(tmp_path, {"IMG_20171345.jpg": b"x"}) == {"IMG_20171345.jpg": None}


def test_broken_sidecar_falls_back_to_the_file_name(tmp_path):
    got = dates(tmp_path, {"IMG_20180101_1.jpg": b"x", "IMG_20180101_1.jpg.json": b"{not json"},
                prefer_from=dt.date(2018, 1, 1))
    assert got == {"IMG_20180101_1.jpg": dt.date(2018, 1, 1)}


def test_sidecars_are_only_read_when_dates_are_asked_for(tmp_path):
    members = {"a.jpg": b"x", "a.jpg.json": taken(dt.datetime(2018, 8, 16, 12))}
    assert dates(tmp_path, members) == {"a.jpg": None}


# --- reading the assets back ------------------------------------------------

@pytest.fixture
def asset(tmp_path):
    make_zip(tmp_path / "t.zip", {"Google Photos/2018/a.jpg": b"jpeg-bytes" * 10})
    return scan_zip_folder(tmp_path, kind)[0]


def test_open_stream_reads_without_a_local_copy(asset, tmp_path):
    with asset.open_stream() as stream:
        assert stream.read() == b"jpeg-bytes" * 10
    assert not (tmp_path / "Google Photos").exists()


def test_materialize_extracts_one_file_and_cleans_up(asset, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with asset.materialize(scratch) as path:
        assert path.name == "a.jpg" and path.read_bytes() == b"jpeg-bytes" * 10
        assert path.parent.parent == scratch
    assert list(scratch.iterdir()) == []


def test_copy_to_keeps_the_takeout_path(asset, tmp_path):
    out = asset.copy_to(tmp_path / "kept")
    assert out == tmp_path / "kept" / "0001_t" / "Google Photos" / "2018" / "a.jpg"
    assert out.read_bytes() == b"jpeg-bytes" * 10


def test_a_zip_that_went_bad_is_a_takeout_error(asset, tmp_path):
    asset.archive.write_bytes(b"gone")
    with pytest.raises(TakeoutError):
        with asset.open_stream():
            pass
    with pytest.raises(TakeoutError):
        with asset.materialize(tmp_path):
            pass
    with pytest.raises(TakeoutError):
        asset.copy_to(tmp_path / "kept")


# --- candidates kept from an earlier pass ----------------------------------

@pytest.fixture
def local(tmp_path):
    path = tmp_path / "kept" / "a.jpg"
    path.parent.mkdir()
    path.write_bytes(b"kept")
    return LocalTakeoutAsset("key", "takeout-001.zip:Google Photos/2018/a.jpg", path, 4, "image", None)


def test_local_asset_name_is_the_member_s_file_name(local):
    assert local.name == "a.jpg"
    assert LocalTakeoutAsset("k", r"t.zip:dir\sub\b.mp4", Path("x"), 1, "video", None).name == "b.mp4"


def test_local_asset_is_read_in_place(local, tmp_path):
    with local.materialize(tmp_path) as path:
        assert path == local.local_path
    with local.open_stream() as stream:
        assert stream.read() == b"kept"


def test_local_asset_copy_to(local, tmp_path):
    out = local.copy_to(tmp_path / "elsewhere")
    assert out == tmp_path / "elsewhere" / "a.jpg" and out.read_bytes() == b"kept"
    # already in the destination: left alone, not copied onto itself
    assert local.copy_to(local.local_path.parent) == local.local_path
