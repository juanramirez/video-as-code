"""loudness.py: `loudness:` brings every clip with sound on an audio
track to one loudness, by a fixed gain - measured on the real render."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from video_as_code import loudness, xmlgen
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve


def test_the_gain():
    assert loudness.gain((-26.0, -20.0), -16) == pytest.approx(10 ** (10 / 20))
    assert loudness.gain((-20.0, -3.0), -16) == pytest.approx(10 ** (2 / 20))  # (its peak: up to -1 dBTP only)
    assert loudness.gain((-60.0, -50.0), -16) == pytest.approx(10 ** (loudness.MAX_BOOST_DB / 20))
    assert loudness.gain((-10.0, -1.0), -16) == pytest.approx(10 ** (-6 / 20))  # (turned down as well)


def _tone(path: Path, freq: int, volume: float) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d=3", "-af", f"volume={volume}",
                    str(path)], check=True)
    return path


def _measure(path: Path, start: float, seconds: float) -> float:
    run = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(seconds), "-i", str(path),
                          "-map", "0:a", "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    return float(run.stderr.rpartition("Summary:")[2].split("I:")[1].split()[0])


def _project(tmp_path: Path, project_extra: str = "loudness: -20", quiet_extra: str = "", track: str = "audio") -> Path:
    _tone(tmp_path / "quiet.wav", 440, 0.3)  # (sine's own level is 1/8: ~-30 LUFS)
    _tone(tmp_path / "loud.wav", 660, 2.5)  # (~-12 LUFS)
    path = tmp_path / "p.yaml"
    path.write_text(f"""
project: {{id: l, title: L, fps: 25, width: 320, height: 240, {project_extra}}}
bin:
  - {{id: quiet, path: quiet.wav}}
  - {{id: loud, path: loud.wav}}
timeline:
  tracks:
    - type: {track}
      clips:
        - {{clip: quiet, start: 0, duration: 3{quiet_extra}}}
        - {{clip: loud, start: 3, duration: 3}}
""", encoding="utf-8")
    return path


def _gains(path: Path) -> dict[str, float]:
    resolved = load_and_resolve(path)
    return {p.bin_clip.id: p.gain for t in resolved.tracks for p in t.placements}


def test_rendered_at_one_loudness(tmp_path, melt):
    path = _project(tmp_path)
    kdenlive, video = tmp_path / "p.kdenlive", tmp_path / "p.mp4"
    xmlgen.write(load_and_resolve(path), kdenlive)
    exe, extra_env = melt
    subprocess.run([exe, str(kdenlive), "-consumer", f"avformat:{video}"], env={**os.environ, **extra_env, "LC_ALL": "C"},
                   capture_output=True, check=True)
    assert _measure(tmp_path / "quiet.wav", 0, 3) < -27 and _measure(tmp_path / "loud.wav", 0, 3) > -15
    assert _measure(video, 0.5, 2) == pytest.approx(-20, abs=0.5)
    assert _measure(video, 3.5, 2) == pytest.approx(-20, abs=0.5)


def test_a_clips_own_target_its_gain_and_false(tmp_path):
    base = _gains(_project(tmp_path))
    assert base["quiet"] > 1 > base["loud"]
    assert _gains(_project(tmp_path, quiet_extra=", loudness: false"))["quiet"] is None
    assert _gains(_project(tmp_path, quiet_extra=", loudness: -26"))["quiet"] == pytest.approx(base["quiet"] / 2, rel=0.01)
    assert _gains(_project(tmp_path, quiet_extra=", gain: 0.5"))["quiet"] == pytest.approx(base["quiet"] / 2)
    assert _gains(_project(tmp_path, project_extra="colorspace: 709")) == {"quiet": None, "loud": None}
    assert _gains(_project(tmp_path, project_extra="colorspace: 709", quiet_extra=", loudness: -20"))["quiet"] == base["quiet"]


def test_not_on_a_video_track(tmp_path):  # (its sound never plays)
    assert _gains(_project(tmp_path, track="video")) == {"quiet": None, "loud": None}


def test_measured_once(tmp_path, monkeypatch):
    path = _project(tmp_path)
    first = _gains(path)
    assert (tmp_path / loudness.CACHE_FILE).is_file()
    monkeypatch.setattr(loudness, "_ffmpeg_measure", lambda *a: pytest.fail("measured again"))
    assert _gains(path) == first


@pytest.mark.parametrize("project_extra, quiet_extra", [("loudness: 3", ""), ("loudness: true", ""), ("", ", loudness: loud")])
def test_wrong_values(tmp_path, project_extra, quiet_extra):
    with pytest.raises(ValidationErrors, match="loudness must be a target loudness in LUFS"):
        load_and_resolve(_project(tmp_path, project_extra=project_extra or "colorspace: 709", quiet_extra=quiet_extra))
