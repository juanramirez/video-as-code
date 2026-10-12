"""Reading a Google Photos *shared album* (a "share by link" album), so a
project's assets can live in the album instead of being copied around by
hand: list what's in it, find an item by its original file name, and
download it at original quality.

Why scraping instead of the official API: since March 2025 the Google
Photos Library API only lets an app read media that app uploaded itself,
and the Picker API only hands out items the user picks interactively, for
the length of one session - neither can re-download "that photo" later,
unattended, on another machine. A shared album's public page can. The
catch: none of what follows is a documented interface. It's what the
page served (and its own JavaScript requests) looked like when this was
written, so if Google changes it, parsing fails loudly (GPhotosError)
rather than downloading the wrong thing.

What the page looks like:

  - The share link (`https://photos.app.goo.gl/...`) redirects to
    `https://photos.google.com/share/<album id>?key=<key>`; both parts are
    needed for every later request.
  - The HTML embeds the first page of items as JSON in an
    `AF_initDataCallback({key: 'ds:1', ... data:<json>, sideChannel: ...})`
    call: `[null, [item, ...], next_page_token, [album id, album title, ...], ...]`.
  - Later pages come from the same `batchexecute` endpoint the page's own
    JavaScript uses, RPC id `snAcKc` with `[album id, token, null, key]`,
    until the token comes back empty.
  - Each item is `[media id, [base url, width, height, ...], taken (UTC
    epoch ms), ..., tz offset (ms), ...]` and ends with a dict of extras;
    videos have a `"76647426"` entry there.
  - `<base url>=d` downloads a photo and `<base url>=dv` a video, both as
    the original file (a video keeps its camera metadata - creation time,
    GPS, `com.android.*` tags - which a re-encode wouldn't), and the
    response's Content-Disposition carries the ORIGINAL file name
    (`PXL_20250830_085416309.mp4`). That name is what projects refer to
    items by (see BinClipSpec.source): stable, human-recognizable and
    searchable in the Photos app, unlike media ids.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from .errors import KdenliveAsCodeError
from .i18n import msg

_PAGE_HOST = "https://photos.google.com"
_LIST_RPC = "snAcKc"
# Key in an item's trailing extras dict that only videos have (holds the
# video's duration, resolution and status).
_VIDEO_EXTRA_KEY = "76647426"
# A plain urllib User-Agent gets a stripped-down page without the embedded
# data, so present as an ordinary desktop browser.
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_TIMEOUT_S = 60
_CHUNK = 1 << 20


class GPhotosError(KdenliveAsCodeError):
    """The shared album couldn't be read or an item couldn't be downloaded."""


@dataclass(frozen=True)
class AlbumItem:
    media_id: str
    base_url: str
    is_video: bool
    width: int
    height: int
    taken_ms: int  # capture time, UTC epoch milliseconds
    tz_offset_ms: int = 0  # the capture's UTC offset, as Google Photos recorded it

    @property
    def download_url(self) -> str:
        return self.base_url + ("=dv" if self.is_video else "=d")


@dataclass(frozen=True)
class AlbumPage:
    album_id: str
    items: list[AlbumItem]
    next_token: Optional[str]


@dataclass(frozen=True)
class _ShareContext:
    """What every request after the share page itself needs."""

    album_id: str
    key: str
    build_label: Optional[str]  # the page's `bl` (server build) parameter
    session_id: Optional[str]  # the page's `f.sid`


# --- parsing (pure, unit-tested without network) ----------------------------


def _item_from_raw(raw: list) -> AlbumItem:
    media = raw[1]
    extras = raw[-1] if isinstance(raw[-1], dict) else {}
    return AlbumItem(
        media_id=str(raw[0]),
        base_url=str(media[0]),
        is_video=_VIDEO_EXTRA_KEY in extras,
        width=int(media[1]),
        height=int(media[2]),
        taken_ms=int(raw[2]),
        tz_offset_ms=int(raw[4]) if len(raw) > 4 and isinstance(raw[4], int) else 0,
    )


def _page_from_data(data: list) -> AlbumPage:
    items = [_item_from_raw(raw) for raw in (data[1] or [])]
    album_id = data[3][0] if len(data) > 3 and data[3] else ""
    return AlbumPage(album_id=str(album_id), items=items, next_token=data[2] or None)


def parse_share_page(html: str) -> AlbumPage:
    """First page of items embedded in a shared album's HTML."""
    match = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", html, re.S)
    if match is None:
        raise GPhotosError(msg("gphotos.page_unrecognized"))
    try:
        return _page_from_data(json.loads(match.group(1)))
    except (ValueError, IndexError, TypeError) as exc:
        raise GPhotosError(msg("gphotos.page_unrecognized")) from exc


def parse_list_response(text: str) -> AlbumPage:
    """A later page of items, from a `batchexecute` response: a `)]}'`
    guard line, then length-prefixed JSON chunks; ours is the
    `["wrb.fr", "snAcKc", "<json string>", ...]` envelope."""
    for line in text.splitlines():
        if not line.startswith('[["wrb.fr"'):
            continue
        try:
            envelope = json.loads(line)[0]
            if envelope[1] == _LIST_RPC and envelope[2]:
                return _page_from_data(json.loads(envelope[2]))
        except (ValueError, IndexError, TypeError) as exc:
            raise GPhotosError(msg("gphotos.page_unrecognized")) from exc
    raise GPhotosError(msg("gphotos.page_unrecognized"))


def filename_from_content_disposition(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    match = re.search(r"filename\*=UTF-8''([^;]+)", header)
    if match:
        return urllib.parse.unquote(match.group(1)).strip()
    match = re.search(r'filename="?([^";]+)"?', header)
    return match.group(1).strip() if match else None


# Camera file names that carry their own capture time: Pixel's
# `PXL_20250830_085416309` (UTC), and the `IMG_`/`VID_` style
# `IMG_20250830_105416` (local time).
_NAME_TIME_RE = re.compile(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})")


def filename_time_hint_ms(name: str) -> Optional[int]:
    """Capture time read from a camera-style file name, as epoch ms taken
    to be UTC (for local-time names the caller also tries the item's own
    UTC offset - see `rank_by_name_time`). None if the name has none."""
    match = _NAME_TIME_RE.search(name)
    if match is None:
        return None
    try:
        when = datetime(*(int(g) for g in match.groups()), tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(when.timestamp() * 1000)


def rank_by_name_time(items: Iterable[AlbumItem], name: str) -> list[AlbumItem]:
    """`items` ordered by how close their capture time is to the one in
    `name`, so finding an item by file name usually needs one or two
    lookups instead of one per album item. Items are only reordered, never
    dropped: Google's capture time for a video can be its END, seconds
    after the time in its name, and a name without a time keeps the
    album's own order."""
    hint = filename_time_hint_ms(name)
    items = list(items)
    if hint is None:
        return items

    def distance(item: AlbumItem) -> int:
        return min(abs(item.taken_ms - hint), abs(item.taken_ms + item.tz_offset_ms - hint))

    return sorted(items, key=distance)


# --- network ------------------------------------------------------------------


def _open(request: urllib.request.Request | str):
    if isinstance(request, str):
        request = urllib.request.Request(request)
    request.add_header("User-Agent", _USER_AGENT)
    try:
        return urllib.request.urlopen(request, timeout=_TIMEOUT_S)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GPhotosError(msg("gphotos.request_failed", url=request.full_url.split("?")[0], exc=exc)) from exc


def _share_context(final_url: str, html: str, album_id: str) -> _ShareContext:
    key = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query).get("key", [""])[0]
    if not key:
        raise GPhotosError(msg("gphotos.not_a_shared_album", url=final_url.split("?")[0]))
    build_label = re.search(r'"cfb2h":"([^"]*)"', html)
    session_id = re.search(r'"FdrFJe":"([^"]*)"', html)
    return _ShareContext(
        album_id=album_id,
        key=key,
        build_label=build_label.group(1) if build_label else None,
        session_id=session_id.group(1) if session_id else None,
    )


def _fetch_next_page(ctx: _ShareContext, token: str) -> AlbumPage:
    params = {"rpcids": _LIST_RPC, "source-path": f"/share/{ctx.album_id}", "rt": "c"}
    if ctx.build_label:
        params["bl"] = ctx.build_label
    if ctx.session_id:
        params["f.sid"] = ctx.session_id
    inner = json.dumps([ctx.album_id, token, None, ctx.key])
    body = urllib.parse.urlencode({"f.req": json.dumps([[[_LIST_RPC, inner, None, "generic"]]])}).encode()
    request = urllib.request.Request(
        f"{_PAGE_HOST}/_/PhotosUi/data/batchexecute?{urllib.parse.urlencode(params)}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    with _open(request) as response:
        return parse_list_response(response.read().decode("utf-8", errors="replace"))


def list_album(share_url: str, on_progress: Optional[Callable[[int], None]] = None) -> list[AlbumItem]:
    """Every item in the shared album at `share_url` (the short
    `photos.app.goo.gl` link or the full `photos.google.com/share/...?key=`
    one), following pagination. `on_progress` gets the running item count."""
    with _open(share_url) as response:
        final_url = response.geturl()
        html = response.read().decode("utf-8", errors="replace")
    page = parse_share_page(html)
    ctx = _share_context(final_url, html, page.album_id)
    items = list(page.items)
    if on_progress:
        on_progress(len(items))
    token = page.next_token
    seen_tokens = set()
    while token and token not in seen_tokens:
        seen_tokens.add(token)
        page = _fetch_next_page(ctx, token)
        items.extend(page.items)
        if on_progress:
            on_progress(len(items))
        token = page.next_token
    return items


def original_filename(item: AlbumItem) -> str:
    """The item's original file name, from the download's
    Content-Disposition (a HEAD request: nothing is downloaded)."""
    with _open(urllib.request.Request(item.download_url, method="HEAD")) as response:
        name = filename_from_content_disposition(response.headers.get("Content-Disposition"))
    if not name:
        raise GPhotosError(msg("gphotos.no_filename", media_id=item.media_id))
    return name


def download(item: AlbumItem, dest: Path) -> tuple[int, str]:
    """Downloads `item` at original quality to `dest` (written to a
    `.part` file first and renamed when complete, so an interrupted
    download never looks finished). Returns (size in bytes, sha256 hex)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with _open(item.download_url) as response, tmp.open("wb") as fh:
            while chunk := response.read(_CHUNK):
                fh.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return size, digest.hexdigest()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
