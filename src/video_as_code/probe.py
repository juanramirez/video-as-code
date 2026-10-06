"""Figures out asset type and duration using ffprobe."""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .i18n import msg

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".svg", ".webp", ".tiff", ".tif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}


class ProbeError(RuntimeError):
    pass


def guess_type(path: Path) -> str:
    """'image' | 'audio' | 'video', based on the file extension."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "video"


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def find_melt() -> str | None:
    """Path to a usable `melt` (the MLT engine kdenlive itself renders
    with), or None if it isn't on PATH. Kdenlive ships it as `melt-7` on
    some distributions (e.g. its own snap package)."""
    return shutil.which("melt") or shutil.which("melt-7")


def melt_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment to run `melt` with: the caller's own (or `base`), plus
    `LC_ALL=C`.

    MLT parses numeric property values with the C library's locale-aware
    conversions at runtime, so under a comma-decimal locale (e.g.
    `LC_NUMERIC=es_ES.UTF-8`) a value like "2.5" is read as 2 - even
    though the generated XML declares `<mlt LC_NUMERIC="C">`. Confirmed by
    real renders (2026-10-06): under es_ES, `gain: 2.5` sounded exactly
    like 2.0 and `gain: 1.5` like 1.0, and the AUDIO of a sped-up clip
    (timewarp, e.g. `speed: 1.08`) played at 1.0x, while video speed was
    unaffected - silently, with no error. Forcing the C locale for the
    melt process makes every decimal in the XML mean what it says."""
    env = dict(os.environ if base is None else base)
    env["LC_ALL"] = "C"
    return env


@functools.lru_cache(maxsize=None)
def probe_duration_seconds(path_str: str) -> float:
    """Duration in seconds of a real audio/video asset (not still images)."""
    path = Path(path_str)
    if not ffprobe_available():
        raise ProbeError(msg("probe.ffprobe_unavailable", path=path))
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ProbeError(msg("probe.ffprobe_failed", path=path, stderr=exc.stderr.strip())) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(msg("probe.ffprobe_timeout", path=path)) from exc

    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ProbeError(msg("probe.duration_unreadable", path=path)) from exc


def render_reversed_segment(path: Path, in_s: float, out_s: float, cache_dir: Path) -> Path:
    """Pre-renders (and caches, keyed by source path + window) a reversed
    copy of [in_s, out_s) from `path` via ffmpeg - the actual workaround
    for `boomerang:`'s reverse half, since MLT's own negative-speed
    timewarp corrupts playback on large images (see
    ClipPlacementSpec.boomerang's docstring in models.py for the full
    root-cause writeup). Playing this file forward at the placement's own
    (positive) speed instead sidesteps MLT's reverse-seek path entirely.
    Idempotent: a second call with the same path/window reuses the cached
    file instead of re-rendering."""
    if not ffmpeg_available():
        raise ProbeError(msg("probe.ffmpeg_unavailable", path=path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{path.resolve()}|{in_s:.6f}|{out_s:.6f}".encode()).hexdigest()[:16]
    ext = path.suffix or ".mp4"
    cache_path = cache_dir / f"{digest}{ext}"
    if cache_path.exists():
        return cache_path
    tmp_path = cache_dir / f"{digest}.tmp{ext}"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(path),
                "-vf", f"trim=start={in_s:.6f}:end={out_s:.6f},setpts=PTS-STARTPTS,reverse",
                "-an",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ProbeError(msg("probe.ffmpeg_reverse_failed", path=path, stderr=exc.stderr.strip())) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(msg("probe.ffmpeg_reverse_timeout", path=path)) from exc
    tmp_path.replace(cache_path)
    return cache_path
