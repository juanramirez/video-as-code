"""Structural tests for scenes.py's `scenes:` compiler: per-clip overrides
(mute/speed/position/size) and audio-only scene routing (new this cycle).
Real-render verification for the audio-dissolve crossfade itself lives in
test_melt_validation.py, matching how the video dissolve/wipe checks are
split between structural (here) and empirical (melt) coverage."""
from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_as_code.errors import ValidationErrors
from kdenlive_as_code.schema import load_and_resolve


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


@pytest.mark.parametrize("transition", ["wipe", "affine", "iris", "flip"])
def test_audio_only_scene_rejects_video_only_transitions(tmp_path: Path, assets_dir: Path, transition: str):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone_a
    path: {assets_dir / "tone.mp3"}
  - id: tone_b
    path: {assets_dir / "tone.mp3"}
scenes:
  - scene: s1
    transition: {transition}
    transition_duration: 0.5
    clips:
      - {{clip: tone_a}}
      - {{clip: tone_b}}
    duration: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert transition in str(exc.value)


def test_affine_transition_invalid_style_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: affine
    style: bounce
    transition_duration: 0.5
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "style" in str(exc.value)


@pytest.mark.parametrize("transition", ["iris", "flip"])
def test_iris_and_flip_only_mark_the_top_clip(tmp_path: Path, assets_dir: Path, transition: str):
    """`iris`/`flip` are single-clip filters (frei0r.alphaspot/3dflippo)
    composited via the qtblend this project already emits per video
    track, not a genuine two-source transition like wipe/affine - only
    the alternating "top" clip (odd index) should carry `reveal_style`;
    the "bottom" one stays untouched, same shape as dissolve."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: {transition}
    transition_duration: 0.5
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    scene_tracks = [t for t in resolved.tracks if t.placements]
    assert len(scene_tracks) == 2
    bottom_placement = scene_tracks[0].placements[0]
    top_placement = scene_tracks[1].placements[0]
    assert bottom_placement.reveal_style is None
    assert top_placement.reveal_style == transition
    assert top_placement.fade_in_frames > 0


def test_glitch_places_every_clip_on_a_single_track(tmp_path: Path, assets_dir: Path):
    """`glitch` (frei0r.glitch0r, new this cycle) writes no alpha at all,
    so a two-track qtblend reveal (like `iris`/`flip` use) would just
    have the fully-opaque "top" clip obscure the bottom one throughout,
    pointlessly - clips stay on one single track instead, every clip
    (not just an alternating "top" one) carrying `reveal_style`, and
    with no real time overlap between them (transition_duration is a
    burst-dressing length, not an overlap)."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: glitch
    transition_duration: 0.4
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    scene_tracks = [t for t in resolved.tracks if t.placements]
    assert len(scene_tracks) == 1
    placements = scene_tracks[0].placements
    assert len(placements) == 2
    assert placements[0].reveal_style == "glitch"
    assert placements[0].fade_out_frames > 0
    assert placements[0].fade_in_frames == 0
    assert placements[1].reveal_style == "glitch"
    assert placements[1].fade_in_frames > 0
    assert placements[1].fade_out_frames == 0
    # No real time overlap: clip 2 starts exactly where clip 1 ends.
    assert placements[1].start_frame == placements[0].start_frame + placements[0].length_frames


def test_glitch_needs_video(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone_a
    path: {assets_dir / "tone.mp3"}
  - id: tone_b
    path: {assets_dir / "tone.mp3"}
scenes:
  - scene: s1
    transition: glitch
    transition_duration: 0.4
    clips:
      - {{clip: tone_a}}
      - {{clip: tone_b}}
    duration: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "glitch" in str(exc.value)


def test_scene_clip_dict_form_carries_chroma_key(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    clips:
      - {{path: {assets_dir / "photo.png"}, chroma_key: "#00ff00", chroma_key_distance: 0.4}}
    duration: 2.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    track = next(t for t in resolved.tracks if t.placements)
    placement = track.placements[0]
    assert placement.chroma_key == "#00ff00"
    assert placement.chroma_key_distance == 0.4
