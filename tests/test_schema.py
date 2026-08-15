from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_generator.errors import ValidationErrors
from kdenlive_generator.schema import load_and_resolve


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "project.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_missing_asset_reported(tmp_path: Path):
    yaml_text = """
project: {id: p1, title: T, fps: 25, width: 320, height: 240}
bin:
  - id: ghost
    path: does_not_exist.mp4
timeline:
  tracks:
    - type: video
      clips:
        - {clip: ghost, start: 0, duration: 1}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "does_not_exist.mp4" in str(exc.value)


def test_out_exceeds_source_duration(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 999}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "exceeds the actual duration" in str(exc.value)


def test_overlap_detected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 5}}
        - {{clip: photo, start: 2, duration: 5}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "overlap" in str(exc.value)


def test_unknown_clip_reference(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: nope, start: 0, duration: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "unknown" in str(exc.value)


def test_title_font_size_must_be_positive(tmp_path: Path):
    yaml_text = """
project: {id: p1, title: T, fps: 25, width: 320, height: 240}
bin:
  - id: t
    type: title
    text: "Hi"
    font_size: 0
timeline:
  tracks:
    - type: video
      clips:
        - {clip: t, start: 0, duration: 1}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "font_size" in str(exc.value)


def test_colorspace_must_be_positive(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240, colorspace: -1}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "colorspace" in str(exc.value)


def test_pip_rect_out_of_canvas_bounds_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1, position: [300, 0], size: [100, 100]}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "canvas" in str(exc.value)


def test_pan_out_of_range_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      pan: 1.5
      clips:
        - {{clip: tone, start: 0, out: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "pan" in str(exc.value)


def test_hidden_and_locked_must_be_boolean(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      hidden: "yes"
      clips:
        - {{clip: photo, start: 0, duration: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "hidden" in str(exc.value)


def test_hidden_and_locked_resolve(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      hidden: true
      locked: true
      clips:
        - {{clip: photo, start: 0, duration: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert resolved.tracks[0].hidden is True
    assert resolved.tracks[0].locked is True


def test_audio_muted_must_be_boolean(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      audio_muted: "yes"
      clips:
        - {{clip: tone, start: 0, out: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "audio_muted" in str(exc.value)


def test_audio_muted_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      audio_muted: true
      clips:
        - {{clip: tone, start: 0, out: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert resolved.tracks[0].audio_muted is True


def test_pan_on_video_track_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      pan: -0.5
      clips:
        - {{clip: photo, start: 0, duration: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "pan" in str(exc.value)


def test_guide_negative_at_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
guides:
  - {{at: -1.0, text: "Bad"}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "at" in str(exc.value)


def test_guide_resolves_to_a_frame(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2}}
guides:
  - {{at: 1.0, text: "Halfway", category: 2}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert len(resolved.guides) == 1
    assert resolved.guides[0].frame == 25
    assert resolved.guides[0].text == "Halfway"
    assert resolved.guides[0].category == 2


def test_transition_unknown_track_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      name: A
      clips:
        - {{clip: photo, start: 0, duration: 2}}
transitions:
  - {{type: wipe, track_a: A, track_b: DoesNotExist, start: 0, end: 1}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "track" in str(exc.value)


def test_transition_resolves_to_frames(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: red
    path: {assets_dir / "photo.png"}
  - id: blue
    path: {assets_dir / "photo2.png"}
timeline:
  tracks:
    - type: video
      name: A
      clips:
        - {{clip: red, start: 0, duration: 2.5}}
    - type: video
      name: B
      clips:
        - {{clip: blue, start: 1.5, duration: 2.5}}
transitions:
  - {{type: affine, track_a: A, track_b: B, start: 1.5, end: 2.5, style: slide}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert len(resolved.affine_transitions) == 1
    at = resolved.affine_transitions[0]
    assert at.track_a == "A"
    assert at.track_b == "B"
    assert at.style == "slide"
    assert at.start_frame == 38  # 1.5s @ 25fps, round-half-up


def test_chroma_key_distance_out_of_range_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1, chroma_key: "#00ff00", chroma_key_distance: 1.5}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "chroma_key_distance" in str(exc.value)


def test_chroma_key_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1, chroma_key: "#00ff00", chroma_key_distance: 0.4}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.chroma_key == "#00ff00"
    assert placement.chroma_key_distance == 0.4


def test_valid_project_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2, fade_in: 0.5, fade_out: 0.5}}
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert resolved.total_frames == 75  # 3s @ 25fps
    assert len(resolved.tracks) == 2
