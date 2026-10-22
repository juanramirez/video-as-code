"""derive: {openshot: [...]}: libopenshot's effects, run by its own Python
(openshot_effects.py, openshot_render.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_as_code import openshot_effects
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve

needs_openshot = pytest.mark.skipif(openshot_effects.python() is None, reason="needs python3-openshot")


def _parse(raw) -> tuple[tuple, list[str]]:
    errors: list[str] = []
    return openshot_effects.parse(raw, "d", errors), errors


@needs_openshot
def test_checked_against_libopenshots_catalog():
    effects, errors = _parse([{"effect": "Wave", "amplitude": 0.6, "wavelength": {0: 0.06, 2: 0.2}},
                              {"effect": "LensFlare", "color": "#ffcc88"}])
    assert errors == [] and len(effects) == 2
    for raw, expected in [
        ([{"effect": "Wavy"}], "Wave"),
        ([{"effect": "Wave", "amplitud": 1}], "amplitude"),
        ([{"effect": "Wave", "amplitude": "big"}], "number"),
        ([{"effect": "LensFlare", "color": [300, 0, 0]}], "color"),
        ("Wave", "list of effects"),
    ]:
        _, errors = _parse(raw)
        assert errors and expected in errors[0], (raw, errors)


def test_without_libopenshot_says_how_to_get_it(monkeypatch):
    monkeypatch.setattr(openshot_effects, "catalog", lambda: {})
    _, errors = _parse([{"effect": "Wave"}])
    assert "python3-openshot" in errors[0]


def _project(tmp_path: Path, source: Path, effects: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: fx
    derive:
      path: {source}
      trim: [0, 1]
      openshot: {effects}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: fx, start: 0, duration: 1}}
""", encoding="utf-8")
    return path


def _mean_luma(path: Path, t: float) -> float:
    data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "format=gray",
                           "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return sum(data) / len(data)


@needs_openshot
def test_a_video_through_an_effect(tmp_path: Path, assets_dir: Path):
    """Negate turns the picture over: the generated clip's mean brightness
    is the original's, inverted (after derive:'s own trim)."""
    resolved = load_and_resolve(_project(tmp_path, assets_dir / "clip.mp4", "[{effect: Negate}]"))
    (clip,) = [c for c in resolved.bin_clips if c.id == "fx"]
    assert clip.path.exists() and clip.path.suffix == ".mp4"
    before, after = _mean_luma(assets_dir / "clip.mp4", 0.5), _mean_luma(clip.path, 0.5)
    assert abs((255 - before) - after) < 25, (before, after)


@needs_openshot
def test_a_still_stays_a_still(tmp_path: Path, assets_dir: Path):
    path = _project(tmp_path, assets_dir / "photo.png", "[{effect: Negate}]").read_text().replace("      trim: [0, 1]\n", "")
    (tmp_path / "project.yaml").write_text(path, encoding="utf-8")
    resolved = load_and_resolve(tmp_path / "project.yaml")
    (clip,) = [c for c in resolved.bin_clips if c.id == "fx"]
    assert clip.path.suffix == ".png"
    r, g, b = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip.path), "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout[:3]
    assert r < 60 and g > 200 and b > 200, (r, g, b)  # red, inverted: cyan


@needs_openshot
def test_rejected_with_upscale(tmp_path: Path, assets_dir: Path):
    text = _project(tmp_path, assets_dir / "photo.png", "[{effect: Negate}]").read_text().replace("trim: [0, 1]", "upscale: 4")
    (tmp_path / "project.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(tmp_path / "project.yaml")
    assert "upscale" in str(exc.value)
