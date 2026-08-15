"""Figures out asset type and duration using ffprobe."""
from __future__ import annotations

import functools
import json
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
