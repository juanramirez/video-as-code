"""Downloads for bin clips with `url:` (see BinClipSpec.url): material
that isn't in the shared album - typically a song - fetched from a link.

Three kinds of link, told apart by host:

- YouTube (youtube.com, youtu.be, music.youtube.com): the AUDIO only,
  through the external `yt-dlp` program, preferring YouTube's own m4a
  stream (`bestaudio[ext=m4a]`) and saving it as served - no re-encoding.
  That choice matters for music that the timeline is cut to: the m4a
  stream starts exactly where the song does, while e.g. the opus/webm one
  can be shifted by tens of milliseconds, enough to put beat-matched cuts
  off. `yt-dlp` breaks often as YouTube changes, and a distro's packaged
  one is usually too old: VIDEO_AS_CODE_YT_DLP overrides the command
  (e.g. "python3 /opt/yt-dlp --js-runtimes node").
- Google Drive (drive.google.com/file/d/<id>/..., ...?id=<id>): the file
  as uploaded. It must be shared as "anyone with the link".
- Anything else over http(s): downloaded as is.

A clip can list several links; fetch.py tries them in order and keeps the
first that works, which is how a fragile source (YouTube) gets a stable
fallback (a copy of the same file on Drive).

Every download lands on a `.part` file first, renamed when complete, so
an interrupted one never looks finished.
"""
from __future__ import annotations

import glob
import http.client
import os
import re
import shlex
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from . import gphotos
from .errors import KdenliveAsCodeError
from .i18n import msg

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
# Drive's direct-download endpoint; `confirm=t` skips the "can't scan this
# file for viruses" page it otherwise puts in front of large files.
_DRIVE_DOWNLOAD = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"
_YT_DLP_ENV = "VIDEO_AS_CODE_YT_DLP"
_YT_DLP_FORMAT = "bestaudio[ext=m4a]/bestaudio"


class LinkError(KdenliveAsCodeError):
    """A link couldn't be downloaded."""


def kind(url: str) -> str:
    """"youtube", "drive" or "http"."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return "youtube"
    if host in _DRIVE_HOSTS:
        return "drive"
    return "http"


def drive_file_id(url: str) -> Optional[str]:
    """The file id in a Drive link: `/file/d/<id>/...` or `?id=<id>`."""
    parsed = urllib.parse.urlparse(url)
    match = re.search(r"/d/([\w-]+)", parsed.path)
    if match:
        return match.group(1)
    ids = urllib.parse.parse_qs(parsed.query).get("id")
    return ids[0] if ids else None


def download(url: str, dest_dir: Path, stem: str) -> Path:
    """Downloads `url` to `dest_dir/<stem><ext>`, the extension being the
    downloaded file's own, and returns that path. Raises LinkError."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    link_kind = kind(url)
    if link_kind == "youtube":
        return _download_youtube(url, dest_dir, stem)
    if link_kind == "drive":
        file_id = drive_file_id(url)
        if not file_id:
            raise LinkError(msg("links.drive_no_id", url=url))
        return _download_http(_DRIVE_DOWNLOAD.format(id=file_id), dest_dir, stem, shown_url=url)
    return _download_http(url, dest_dir, stem, shown_url=url)


def _yt_dlp_command() -> list[str]:
    return shlex.split(os.environ.get(_YT_DLP_ENV) or "yt-dlp")


def _download_youtube(url: str, dest_dir: Path, stem: str) -> Path:
    for stale in dest_dir.glob(f"{glob.escape(stem)}.part.*"):  # leftovers of an interrupted run
        stale.unlink()
    command = _yt_dlp_command() + [
        "--quiet", "--no-warnings", "--no-playlist", "--no-progress",
        "-f", _YT_DLP_FORMAT,
        "-o", str(dest_dir / f"{stem}.part.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]
    try:
        run = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise LinkError(msg("links.yt_dlp_missing", command=command[0], env=_YT_DLP_ENV, exc=exc)) from exc
    lines = [line for line in run.stdout.splitlines() if line.strip()]
    if run.returncode != 0 or not lines or not Path(lines[-1]).is_file():
        detail = (run.stderr.strip().splitlines() or ["?"])[-1]
        raise LinkError(msg("links.yt_dlp_failed", url=url, detail=detail))
    part = Path(lines[-1])
    final = dest_dir / f"{stem}{part.suffix.lower()}"
    part.replace(final)
    return final


def _download_http(url: str, dest_dir: Path, stem: str, shown_url: str) -> Path:
    tmp = dest_dir / f"{stem}.part"
    try:
        # (gphotos' opener: same browser User-Agent and error reporting)
        with gphotos._open(url) as response, tmp.open("wb") as fh:
            if "text/html" in (response.headers.get("Content-Type") or ""):
                # what Drive serves for a file that isn't shared by link
                raise LinkError(msg("links.got_a_page", url=shown_url))
            name = gphotos.filename_from_content_disposition(response.headers.get("Content-Disposition"))
            suffix = Path(name or urllib.parse.urlparse(response.geturl()).path).suffix.lower()
            try:
                while chunk := response.read(1 << 20):
                    fh.write(chunk)
            except (OSError, http.client.HTTPException) as exc:
                # dropped or stalled halfway (gphotos._open only covers the request)
                raise LinkError(msg("links.download_interrupted", url=shown_url, exc=exc)) from exc
    except gphotos.GPhotosError as exc:
        tmp.unlink(missing_ok=True)
        raise LinkError(str(exc)) from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    final = dest_dir / f"{stem}{suffix}"
    tmp.replace(final)
    return final
