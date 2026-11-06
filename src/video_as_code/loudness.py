"""`loudness:`: every clip with sound brought to the same loudness.

Each clip on an audio track is measured (EBU R128's integrated loudness,
ffmpeg's `ebur128` filter, over the stretch of its file the clip plays)
and given the static gain that takes it to the target - a `volume`
filter like `gain:`'s, so kdenlive shows it and nothing is re-encoded.
Not a compressor: within a clip, loud and quiet parts keep their
difference; what's evened out is one clip against another (a quiet
phone video against a loud song).

The gain never takes a clip's true peak over PEAK_CEILING_DB (it would
clip): such a clip stays quieter than the target. And never over
MAX_BOOST_DB: a clip that's almost silent isn't turned into loud
background hiss.

Measurements are cached by file (path, size, mtime) and stretch, in
<project dir>/.render_cache/loudness.json: a render measures a file
once, not on every build.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Optional

PEAK_CEILING_DB = -1.0
MAX_BOOST_DB = 20.0
CACHE_FILE = Path(".render_cache") / "loudness.json"
# below this, ebur128 has nothing to measure (silence, or no sound at all)
SILENCE_LUFS = -70.0


def _key(path: Path, start_s: Optional[float], end_s: Optional[float]) -> str:
    st = path.stat()
    stretch = "whole" if start_s is None else f"{start_s:.3f}-{end_s:.3f}"
    return f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{stretch}"


def _ffmpeg_measure(path: Path, start_s: Optional[float], end_s: Optional[float]) -> Optional[tuple[float, float]]:
    """(integrated loudness in LUFS, true peak in dBFS) - None without sound."""
    window = [] if start_s is None else ["-ss", f"{start_s:.3f}", "-t", f"{end_s - start_s:.3f}"]
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *window, "-i", str(path), "-map", "0:a:0?",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    summary = run.stderr.rpartition("Summary:")[2]
    loudness = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if run.returncode != 0 or not loudness or not peak:
        return None
    value = float(loudness.group(1))
    if not math.isfinite(value) or value <= SILENCE_LUFS:
        return None
    return value, float(peak.group(1))


class Meter:
    """Measures, through the cache of a project's folder."""

    def __init__(self, project_dir: Path) -> None:
        self.path = project_dir / CACHE_FILE
        try:
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.changed = False

    def measure(self, path: Path, start_s: Optional[float] = None, end_s: Optional[float] = None) -> Optional[tuple[float, float]]:
        key = _key(path, start_s, end_s)
        if key not in self.cache:
            self.cache[key] = _ffmpeg_measure(path, start_s, end_s)
            self.changed = True
        found = self.cache[key]
        return tuple(found) if found else None

    def save(self) -> None:
        if not self.changed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            pass  # (measured again next time)


def gain(measured: tuple[float, float], target_lufs: float) -> float:
    """The linear factor taking `measured` (loudness, peak) to `target_lufs`."""
    loudness, peak = measured
    db = min(target_lufs - loudness, PEAK_CEILING_DB - peak, MAX_BOOST_DB)
    return 10 ** (db / 20)
