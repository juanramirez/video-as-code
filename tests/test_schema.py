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
