"""gphotos.py against synthetic pages shaped like Google Photos' own (see
the module docstring) - no network: `_open` is replaced by canned responses."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from video_as_code import gphotos

ALBUM_ID = "AF1QipAlbum"
KEY = "secretkey"
SHARE_URL = f"https://photos.google.com/share/{ALBUM_ID}?key={KEY}"


def _raw_item(media_id: str, taken_ms: int, video: bool = False, tz_offset_ms: int = 7200000) -> list:
    extras = {"15": 1, "525000002": [[ALBUM_ID]]}
    if video:
        extras["76647426"] = [14501, None, 1920, 1080]
    return [
        media_id,
        [f"https://lh3.googleusercontent.com/pw/{media_id}", 1920, 1080, None, None, None, None, None, [None, None, 1], [123]],
        taken_ms,
        "dedup",
        tz_offset_ms,
        taken_ms + 1000,
        extras,
    ]


def _page_data(items: list, token: str | None) -> list:
    return [None, items, token, [ALBUM_ID, "Album title", [0, 0]]]


def _share_html(items: list, token: str | None) -> str:
    data = json.dumps(_page_data(items, token))
    return (
        '<html><script>window.WIZ_global_data = {"cfb2h":"build_1","FdrFJe":"42"};</script>'
        f"<script>AF_initDataCallback({{key: 'ds:0', hash: '1', data:[], sideChannel: {{}}}});</script>"
        f"<script>AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});</script></html>"
    )


def _batch_response(items: list, token: str | None) -> str:
    envelope = json.dumps([["wrb.fr", "snAcKc", json.dumps(_page_data(items, token)), None, None, None, "generic"]])
    return f")]}}'\n\n{len(envelope)}\n{envelope}\n25\n[[\"e\",4,null,null,123]]\n"


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str = "", headers: dict | None = None):
        super().__init__(body)
        self._url = url
        self.headers = headers or {}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_parse_share_page_reads_items_and_token():
    page = gphotos.parse_share_page(_share_html([_raw_item("p1", 1000), _raw_item("v1", 2000, video=True)], "tok"))
    assert page.album_id == ALBUM_ID
    assert page.next_token == "tok"
    photo, video = page.items
    assert (photo.media_id, photo.is_video, photo.taken_ms, photo.tz_offset_ms) == ("p1", False, 1000, 7200000)
    assert photo.download_url.endswith("/p1=d")
    assert video.is_video and video.download_url.endswith("/v1=dv")


def test_parse_share_page_rejects_unknown_layout():
    with pytest.raises(gphotos.GPhotosError):
        gphotos.parse_share_page("<html>no data here</html>")


def test_parse_list_response_last_page_has_no_token():
    page = gphotos.parse_list_response(_batch_response([_raw_item("p2", 3000)], None))
    assert [i.media_id for i in page.items] == ["p2"]
    assert page.next_token is None


def test_list_album_follows_pagination(monkeypatch):
    requests = []

    def fake_open(request):
        url = request if isinstance(request, str) else request.full_url
        requests.append(request)
        if "batchexecute" in url:
            inner = json.loads(json.loads(dict(_form(request.data))["f.req"])[0][0][1])
            assert inner == [ALBUM_ID, "tok1", None, KEY]
            assert "bl=build_1" in url and "f.sid=42" in url
            return _FakeResponse(_batch_response([_raw_item("p2", 3000)], None).encode())
        return _FakeResponse(_share_html([_raw_item("p1", 1000)], "tok1").encode(), url=SHARE_URL)

    monkeypatch.setattr(gphotos, "_open", fake_open)
    counts = []
    items = gphotos.list_album("https://photos.app.goo.gl/short", on_progress=counts.append)
    assert [i.media_id for i in items] == ["p1", "p2"]
    assert counts == [1, 2]
    assert len(requests) == 2


def _form(data: bytes):
    from urllib.parse import parse_qsl

    return parse_qsl(data.decode())


def test_list_album_needs_a_share_key(monkeypatch):
    monkeypatch.setattr(
        gphotos, "_open",
        lambda request: _FakeResponse(_share_html([], None).encode(), url=f"https://photos.google.com/share/{ALBUM_ID}"),
    )
    with pytest.raises(gphotos.GPhotosError):
        gphotos.list_album("https://photos.app.goo.gl/short")


@pytest.mark.parametrize(
    "header, expected",
    [
        ('attachment;filename="PXL_20250829_225751311.NIGHT.jpg"', "PXL_20250829_225751311.NIGHT.jpg"),
        ('attachment; filename="PXL_20250830_085416309.mp4"', "PXL_20250830_085416309.mp4"),
        ("attachment; filename*=UTF-8''v%C3%ADdeo.mp4", "vídeo.mp4"),
        (None, None),
    ],
)
def test_filename_from_content_disposition(header, expected):
    assert gphotos.filename_from_content_disposition(header) == expected


def test_original_filename_uses_a_head_request(monkeypatch):
    seen = {}

    def fake_open(request):
        seen["method"] = request.get_method()
        return _FakeResponse(b"", headers={"Content-Disposition": 'attachment; filename="IMG_1.jpg"'})

    monkeypatch.setattr(gphotos, "_open", fake_open)
    item = gphotos.parse_share_page(_share_html([_raw_item("p1", 1000)], None)).items[0]
    assert gphotos.original_filename(item) == "IMG_1.jpg"
    assert seen["method"] == "HEAD"


def test_rank_by_name_time_puts_the_closest_capture_first():
    # PXL_ names are UTC: 2025-08-30 08:54:16.309
    hint = gphotos.filename_time_hint_ms("PXL_20250830_085416309.mp4")
    far = gphotos.parse_share_page(_share_html([_raw_item("far", hint - 86400000)], None)).items[0]
    # a video's capture time can be its end, a few seconds after its name's
    near = gphotos.parse_share_page(_share_html([_raw_item("near", hint + 17000, video=True)], None)).items[0]
    assert [i.media_id for i in gphotos.rank_by_name_time([far, near], "PXL_20250830_085416309.mp4")] == ["near", "far"]


def test_rank_by_name_time_understands_local_time_names():
    # IMG_ names are local time: 10:54:16 at UTC+2 is 08:54:16 UTC
    utc_ms = gphotos.filename_time_hint_ms("PXL_20250830_085416000.jpg")
    item = gphotos.parse_share_page(_share_html([_raw_item("local", utc_ms, tz_offset_ms=7200000)], None)).items[0]
    other = gphotos.parse_share_page(_share_html([_raw_item("other", utc_ms + 3600000)], None)).items[0]
    assert gphotos.rank_by_name_time([other, item], "IMG_20250830_105416.jpg")[0].media_id == "local"


def test_rank_by_name_time_keeps_order_without_a_time_in_the_name():
    items = gphotos.parse_share_page(_share_html([_raw_item("a", 2), _raw_item("b", 1)], None)).items
    assert [i.media_id for i in gphotos.rank_by_name_time(items, "musica.mp3")] == ["a", "b"]


def test_download_writes_atomically_and_hashes(tmp_path: Path, monkeypatch):
    body = b"x" * 3_000_000
    monkeypatch.setattr(gphotos, "_open", lambda request: _FakeResponse(body))
    item = gphotos.parse_share_page(_share_html([_raw_item("p1", 1000)], None)).items[0]
    dest = tmp_path / "assets" / "IMG_1.jpg"
    size, sha = gphotos.download(item, dest)
    assert (size, sha) == (len(body), hashlib.sha256(body).hexdigest())
    assert dest.read_bytes() == body
    assert not dest.with_name("IMG_1.jpg.part").exists()
    assert gphotos.sha256_of(dest) == sha


@pytest.mark.parametrize("error", [OSError("connection reset"), TimeoutError("The read operation timed out")])
def test_download_leaves_nothing_behind_when_interrupted(tmp_path: Path, monkeypatch, error: Exception):
    # a connection broken halfway is this item's GPhotosError (so fetch goes
    # on with the rest), not an exception that stops everything
    class Broken(_FakeResponse):
        def read(self, *args):
            raise error

    monkeypatch.setattr(gphotos, "_open", lambda request: Broken(b""))
    item = gphotos.parse_share_page(_share_html([_raw_item("p1", 1000)], None)).items[0]
    dest = tmp_path / "IMG_1.jpg"
    with pytest.raises(gphotos.GPhotosError, match="broke off"):
        gphotos.download(item, dest)
    assert not dest.exists() and not dest.with_name("IMG_1.jpg.part").exists()
