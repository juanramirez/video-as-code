"""Structural tests for scenes.py's `scenes:` compiler: per-clip overrides
(mute/speed/position/size) and audio-only scene routing (new this cycle).
Real-render verification for the audio-dissolve crossfade itself lives in
test_melt_validation.py, matching how the video dissolve/wipe checks are
split between structural (here) and empirical (melt) coverage."""
from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_generator.errors import ValidationErrors
from kdenlive_generator.schema import load_and_resolve


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "project.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_scene_clip_dict_form_carries_mute_and_position(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    clips:
      - {{path: {assets_dir / "photo.png"}, mute: true, position: [10, 20], size: [100, 50]}}
    duration: 2.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    track = next(t for t in resolved.tracks if t.placements)
    placement = track.placements[0]
    assert placement.mute is True
    assert placement.rect == (10.0, 20.0, 100.0, 50.0)


def test_scene_clip_dict_form_carries_speed(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    clips:
      - {{path: {assets_dir / "clip.mp4"}, speed: 2.0, preserve_pitch: true}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    track = next(t for t in resolved.tracks if t.placements)
    placement = track.placements[0]
    assert placement.speed == 2.0
    assert placement.preserve_pitch is True


def test_audio_only_scene_routes_to_an_audio_track(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone_a
    path: {assets_dir / "tone.mp3"}
  - id: tone_b
    path: {assets_dir / "tone.mp3"}
scenes:
  - scene: s1
    transition: dissolve
    transition_duration: 0.5
    clips:
      - {{clip: tone_a}}
      - {{clip: tone_b}}
    duration: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    scene_tracks = [t for t in resolved.tracks if t.placements]
    assert len(scene_tracks) == 2
    assert all(t.kind == "audio" for t in scene_tracks)


def test_audio_only_scene_rejects_wipe(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone_a
    path: {assets_dir / "tone.mp3"}
  - id: tone_b
    path: {assets_dir / "tone.mp3"}
scenes:
  - scene: s1
    transition: wipe
    transition_duration: 0.5
    clips:
      - {{clip: tone_a}}
      - {{clip: tone_b}}
    duration: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "wipe" in str(exc.value)
