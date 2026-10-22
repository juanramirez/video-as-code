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
    some distributions (e.g. its own snap package). VIDEO_AS_CODE_MELT
    points at another one - e.g. an MLT built from source, newer than the
    distribution's (its plugins found through MLT_REPOSITORY, which melt
    reads from the environment as usual)."""
    override = os.environ.get("VIDEO_AS_CODE_MELT")
    if override:
        return override if os.path.isfile(override) else None
    return shutil.which("melt") or shutil.which("melt-7")


def frei0r_dirs(root: Path = Path("/")) -> list[str]:
    """Where frei0r plugins are, for FREI0R_PATH. MLT's frei0r module
    only looks in /usr/lib/frei0r-1, /usr/local/lib/frei0r-1 and
    ~/.frei0r-1/lib - not Debian/Ubuntu's multiarch
    /usr/lib/<triplet>/frei0r-1, where the frei0r-plugins package puts
    them: found 2026-10-22, with Ubuntu's own melt 7.36 listing no
    frei0r filter at all, so every frei0r-backed one (iris, flip,
    glitch, delaygrab, cairogradient, chroma_key) rendered as nothing,
    silently. Setting FREI0R_PATH replaces those defaults, so they're
    kept in it too."""
    candidates = [
        *sorted((root / "usr" / "lib").glob("*/frei0r-1")),
        root / "usr" / "lib" / "frei0r-1",
        root / "usr" / "local" / "lib" / "frei0r-1",
        Path.home() / ".frei0r-1" / "lib",
    ]
    return [str(d) for d in candidates if d.is_dir()]


def melt_env(base: dict[str, str] | None = None, mlt_data: Path | None = None) -> dict[str, str]:
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
    melt process makes every decimal in the XML mean what it says.

    Also FREI0R_PATH, unless the caller set it (see frei0r_dirs), with
    the gl-transitions plugin's folder first once it's built
    (gltransitions.py)."""
    env = dict(os.environ if base is None else base)
    env["LC_ALL"] = "C"
    frei0r_path = env["FREI0R_PATH"].split(":") if env.get("FREI0R_PATH") else frei0r_dirs()
    # this project's own frei0r plugin (transition: gl), when built
    from .gltransitions import PLUGIN, install_dir

    if (install_dir() / PLUGIN).is_file() and str(install_dir()) not in frei0r_path:
        frei0r_path = [str(install_dir()), *frei0r_path]
    if frei0r_path:
        env["FREI0R_PATH"] = ":".join(frei0r_path)
    if mlt_data is not None:
        env["MLT_DATA"] = str(mlt_data)  # see mltdata.py
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


# Sampling grid for `detect_trailing_hold_seconds`: frames are compared as tiny grayscale thumbnails
# (averaging away compression noise - a genuinely frozen frame re-encoded by h264 still differs by a
# fraction of a level per pixel), resampled at a fixed rate so a gif's variable frame delays and a
# video's real frame rate end up on the same time grid.
HOLD_SAMPLE_FPS = 30
_HOLD_THUMB_W, _HOLD_THUMB_H = 32, 18
# Mean absolute difference (0-255 gray levels, per thumbnail pixel) at or under which a sampled frame
# counts as "the same picture" as the window's last one. Calibrated on real-world boomerang clips:
# held gif frames come out at 0.0, while consecutive frames of even a slow-moving video differ by ~1-2.
HOLD_DIFF_THRESHOLD = 1.0
# Shortest hold `boomerang_trim_hold` acts on. A source that keeps moving to the end still "holds" its
# last frame for one source frame, which resampling to HOLD_SAMPLE_FPS can stretch to ~0.08s (seen on a
# 25fps testsrc) - doubling that at the turnaround is invisible. The holds worth halving (gif final
# delays, frozen video tails) measured 0.2-0.75s on real-world clips.
HOLD_MIN_S = 0.1


def detect_trailing_hold_seconds(path: Path, in_s: float, out_s: float, cache_dir: Path) -> float:
    """How long the LAST picture of [in_s, out_s) from `path` stays on screen unchanged, in source
    seconds (0.0 if it changes right up to the end, or if the whole window is one still picture -
    nothing to trim there). Used by `boomerang:` (see ClipPlacementSpec.boomerang_trim_hold): the
    forward half ends on that picture and the reverse half starts on it, so a held last frame (a gif's
    long final delay, a video's frozen tail) shows twice as long at the turnaround as it did in the
    source.

    Measured from where the last change happens to `out_s` itself, not to the end of the sampled
    frames: ffmpeg's fps filter emits a gif's last frame for its FULL delay even when `out_s` cuts it
    short, so counting sampled frames alone would overestimate the hold. Cached (JSON, keyed by source
    path + mtime + window) next to the reversed derivatives, since decoding is the slow part."""
    if not ffmpeg_available():
        raise ProbeError(msg("probe.ffmpeg_unavailable", path=path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    key = f"hold|{resolved}|{resolved.stat().st_mtime_ns}|{in_s:.6f}|{out_s:.6f}|{HOLD_DIFF_THRESHOLD}"
    cache_path = cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.hold.json"
    if cache_path.exists():
        try:
            return float(json.loads(cache_path.read_text())["hold_s"])
        except (ValueError, KeyError):
            pass  # corrupt cache entry: recompute and overwrite below
    try:
        raw = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error",
                "-i", str(path),
                "-vf",
                f"trim=start={in_s:.6f}:end={out_s:.6f},setpts=PTS-STARTPTS,fps={HOLD_SAMPLE_FPS},"
                f"scale={_HOLD_THUMB_W}:{_HOLD_THUMB_H},format=gray",
                "-an", "-f", "rawvideo", "-",
            ],
            capture_output=True,
            timeout=120,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise ProbeError(
            msg("probe.ffmpeg_hold_failed", path=path, stderr=exc.stderr.decode(errors="replace").strip())
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(msg("probe.ffmpeg_hold_timeout", path=path)) from exc

    size = _HOLD_THUMB_W * _HOLD_THUMB_H
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    hold_s = 0.0
    if frames:
        last = frames[-1]
        same = 0
        for frame in reversed(frames):
            if sum(abs(a - b) for a, b in zip(frame, last)) / size > HOLD_DIFF_THRESHOLD:
                break
            same += 1
        if same < len(frames):  # an all-same window is a still picture: leave it alone
            last_change_s = (len(frames) - same) / HOLD_SAMPLE_FPS
            hold_s = max(0.0, (out_s - in_s) - last_change_s)
    cache_path.write_text(json.dumps({"hold_s": hold_s}))
    return hold_s
