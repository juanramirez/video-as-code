from __future__ import annotations

from pathlib import Path

import pytest

from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve


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


@pytest.mark.parametrize("luma", ["heart", "bowtie"])
def test_transition_wipe_accepts_new_luma_shapes(tmp_path: Path, assets_dir: Path, luma: str):
    """`heart`/`bowtie` (v0.8's new procedural luma shapes, lumas.py) are
    plain additions to `AVAILABLE_LUMAS` - no new validation code path,
    just confirms the low-level `transitions:` `wipe` shape accepts them
    the same way it already accepts every other named luma."""
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
  - {{type: wipe, track_a: A, track_b: B, start: 1.5, end: 2.5, luma: {luma}}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert len(resolved.wipe_transitions) == 1
    assert resolved.wipe_transitions[0].luma == luma


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


def test_ken_burns_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          ken_burns_from: [0, 0, 100, 100]
          ken_burns_to: [-25, -25, 150, 150]
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.ken_burns_from == (0.0, 0.0, 100.0, 100.0)
    assert placement.ken_burns_to == (-25.0, -25.0, 150.0, 150.0)


def test_ken_burns_requires_both_from_and_to(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          ken_burns_from: [0, 0, 100, 100]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "ken_burns" in str(exc.value)


def test_ken_burns_zero_size_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          ken_burns_from: [0, 0, 100, 100]
          ken_burns_to: [0, 0, 0, 100]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "ken_burns" in str(exc.value)


def test_focus_rect_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          focus_rect: [35, 35, 30, 30]
          focus_blur: 20
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.focus_rect == (35.0, 35.0, 30.0, 30.0)
    assert placement.focus_blur == 20.0


def test_focus_rect_zero_size_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          focus_rect: [35, 35, 0, 30]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "focus_rect" in str(exc.value)


def test_focus_blur_out_of_range_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          focus_rect: [35, 35, 30, 30]
          focus_blur: 999
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "focus_blur" in str(exc.value)


def test_old_film_flags_resolve(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          old_film: true
          film_grain: true
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.old_film is True
    assert placement.film_grain is True
    assert placement.film_dust is None
    assert placement.film_scratches is None


def test_old_film_flags_must_be_boolean(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          film_dust: "yes"
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "film_dust" in str(exc.value)


def test_color_grade_partial_groups_resolve(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          color_grade:
            gain: [1.2, 1.0, 0.9]
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.color_grade == {"gain": (1.2, 1.0, 0.9)}


def test_color_grade_all_groups_resolve(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          color_grade:
            lift: [0.05, 0.0, 0.0]
            gamma: [1.1, 1.0, 1.0]
            gain: [1.0, 1.0, 1.2]
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.color_grade == {
        "lift": (0.05, 0.0, 0.0),
        "gamma": (1.1, 1.0, 1.0),
        "gain": (1.0, 1.0, 1.2),
    }


def test_color_grade_empty_dict_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          color_grade: {{}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "color_grade" in str(exc.value)


def test_color_grade_unknown_group_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          color_grade:
            saturation: [1.0, 1.0, 1.0]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "color_grade" in str(exc.value)


def test_color_grade_bad_triple_shape_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          color_grade:
            gain: [1.0, 1.0]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "color_grade" in str(exc.value)


def test_color_grade_negative_value_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          color_grade:
            lift: [-0.1, 0.0, 0.0]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "color_grade" in str(exc.value)


def test_stabilize_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          stabilize: true
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.stabilize is True


def test_stabilize_must_be_boolean(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1
          stabilize: "yes"
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "stabilize" in str(exc.value)


def test_speed_ramp_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 2.0
          speed_ramp:
            - {{at: 0, speed: 1.0}}
            - {{at: 1, speed: 2.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.speed is None
    assert placement.speed_ramp == [(0, 1.0), (25, 2.0)]
    assert placement.out_frame - placement.in_frame + 1 == 50  # 2.0s @ 25fps


def test_speed_ramp_conflicts_with_speed_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          speed: 2.0
          speed_ramp:
            - {{at: 0, speed: 1.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_speed_ramp_requires_duration_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          speed_ramp:
            - {{at: 0, speed: 1.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_speed_ramp_must_start_at_zero_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 2.0
          speed_ramp:
            - {{at: 0.5, speed: 1.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_speed_ramp_not_sorted_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 2.0
          speed_ramp:
            - {{at: 0, speed: 1.0}}
            - {{at: 1, speed: 2.0}}
            - {{at: 0.5, speed: 3.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_speed_ramp_exceeds_source_rejected(tmp_path: Path, assets_dir: Path):
    """clip.mp4 (assets_dir) is 3s - a flat 10x ramp over a 2s output
    would need 20s of source material, far more than exists."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 2.0
          speed_ramp:
            - {{at: 0, speed: 10.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_source_in_out_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          speed: 2.0
          source_in: 1.0
          source_out: 3.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    # in_s = 1.0/2.0 = 0.5s = 12.5 frames -> round-half-up 13; out_s = 3.0/2.0 = 1.5s = 37.5 -> 38, last frame 37
    assert placement.in_frame == 13
    assert placement.out_frame == 37


def test_source_in_out_incomplete_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 1.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "source_in" in str(exc.value)


def test_source_in_out_conflicts_with_in_out_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 1.0, source_out: 2.0, in: 0.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "source_in" in str(exc.value)


def test_source_in_out_invalid_range_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 2.0, source_out: 1.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "source_in" in str(exc.value) or "source_out" in str(exc.value)


def test_source_in_out_conflicts_with_speed_ramp_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 1.0
          source_in: 0.5
          source_out: 1.0
          speed_ramp:
            - {{at: 0, speed: 2.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    # Rejected via the source_in/source_out-vs-duration conflict check -
    # speed_ramp itself requires an explicit duration/out, and
    # source_in/source_out reject being combined with one, so the two
    # are mutually exclusive by construction (see schema.py).
    assert "source_in" in str(exc.value)



def test_speed_ramp_with_source_in_resolves(tmp_path: Path, assets_dir: Path):
    """`source_in:` alone (no `source_out:`) is the supported way to start
    a ramp later in the source - it only offsets where the ramp reads
    from, the placement's own range still starts at 0."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 1.0
          source_in: 1.0
          speed_ramp:
            - {{at: 0, speed: 1.0}}
            - {{at: 0.5, speed: 2.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.speed_ramp_source_offset_s == 1.0
    assert placement.in_frame == 0
    assert placement.out_frame - placement.in_frame + 1 == 25  # 1.0s @ 25fps


def test_speed_ramp_with_source_in_exceeds_source_rejected(tmp_path: Path, assets_dir: Path):
    """clip.mp4 (assets_dir) is 3s - starting at 2.5s leaves only 0.5s,
    so a flat 1x ramp over 1s of output runs out of source."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 1.0
          source_in: 2.5
          speed_ramp:
            - {{at: 0, speed: 1.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_position_keyframes_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 2.0
          position_keyframes:
            - {{at: 0, position: [0, 0], size: [60, 60]}}
            - {{at: 1, position: [260, 180], size: [60, 60]}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.position_keyframes == [(0, 0.0, 0.0, 60.0, 60.0), (25, 260.0, 180.0, 60.0, 60.0)]


def test_position_keyframes_conflicts_with_position_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          position: [0, 0]
          position_keyframes:
            - {{at: 0, position: [0, 0], size: [60, 60]}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "position_keyframes" in str(exc.value)


def test_position_keyframes_must_start_at_zero_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          position_keyframes:
            - {{at: 0.5, position: [0, 0], size: [60, 60]}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "position_keyframes" in str(exc.value)


def test_position_keyframes_out_of_bounds_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          position_keyframes:
            - {{at: 0, position: [300, 0], size: [100, 100]}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "position_keyframes" in str(exc.value)


def test_freeze_before_after_without_at_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          freeze_after: true
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "freeze_at" in str(exc.value)


def test_freeze_at_negative_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          freeze_at: -0.5
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "freeze_at" in str(exc.value)


def test_freeze_at_out_of_bounds_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          freeze_at: 5.0
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "freeze_at" in str(exc.value)


def test_freeze_at_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: clip
          start: 0
          duration: 1.0
          freeze_at: 0.5
          freeze_after: true
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.freeze_at_frame == 13  # 0.5s @ 25fps = 12.5 frames, round-half-up
    assert placement.freeze_after is True
    assert placement.freeze_before is False


def test_speed_ramp_on_image_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - clip: photo
          start: 0
          duration: 1.0
          speed_ramp:
            - {{at: 0, speed: 2.0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "speed_ramp" in str(exc.value)


def test_overlay_font_size_must_be_positive(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1, overlay_text: "#timecode#", overlay_font_size: 0}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "overlay_font_size" in str(exc.value)


def test_overlay_text_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1, overlay_text: "#timecode#", overlay_font_size: 32}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placement = resolved.tracks[0].placements[0]
    assert placement.overlay_text == "#timecode#"
    assert placement.overlay_font_size == 32


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


def test_fade_in_level_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_in: 1.0, fade_in_level: 0.4}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert resolved.tracks[0].placements[0].fade_in_level == 0.4


def test_fade_in_level_out_of_range_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_in: 1.0, fade_in_level: 1.5}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "fade_in_level" in str(exc.value)


def test_fade_out_level_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 1.0, fade_out_level: 0.5}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert resolved.tracks[0].placements[0].fade_out_level == 0.5


def test_fade_out_level_out_of_range_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 1.0, fade_out_level: -0.2}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "fade_out_level" in str(exc.value)


def test_gain_resolves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, gain: 1.5}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    assert resolved.tracks[0].placements[0].gain == 1.5


def test_gain_negative_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, gain: -0.5}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "gain" in str(exc.value)


def test_boomerang_expands_into_forward_and_reverse(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 1.0, source_in: 0, source_out: 2, speed: 2.0, fade_in: 0.5, mute: true, boomerang: true}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    placements = resolved.tracks[0].placements
    assert len(placements) == 2
    forward, reverse = placements
    # forward: as written, just with boomerang consumed - own duration (2-0)/2.0 = 1.0s
    assert forward.start_frame == pytest.approx(25.0, abs=1)
    # reverse: starts right where the forward one ends, hard cut, forced mute, SAME (positive)
    # speed as forward - no negation (see ClipPlacementSpec.boomerang: the reverse half plays a
    # pre-reversed derivative file forward instead of using MLT's own negative-speed timewarp)
    assert reverse.start_frame == forward.start_frame + forward.length_frames
    assert reverse.speed == 2.0
    assert reverse.fade_in_frames == 0
    assert reverse.mute is True
    # the reverse half's bin clip is a distinct (cached, pre-reversed) derivative file, not the
    # original source
    assert reverse.bin_clip.path != forward.bin_clip.path
    assert reverse.bin_clip.path.exists()


def _held_gif_boomerang_yaml(assets_dir: Path, extra: str = "") -> str:
    return f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "held.gif"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 0, source_out: 1.0, mute: true, boomerang: true{extra}}}
"""


def test_boomerang_trims_half_of_a_held_last_frame(tmp_path: Path, assets_dir: Path):
    # held.gif holds its last picture for 0.6s: without trimming, the turnaround would show it for
    # 1.2s (end of forward + start of reverse). Trimming 0.3s off source_out on BOTH halves leaves
    # 0.3 + 0.3 = 0.6s at the turnaround - the hold's original length, shown once.
    path = _write_yaml(tmp_path, _held_gif_boomerang_yaml(assets_dir))
    forward, reverse = load_and_resolve(path).tracks[0].placements
    # 1.0 - 0.3 = 0.7s = 17.5 frames at 25fps (one frame of tolerance for the 30fps sampling grid)
    assert forward.length_frames == pytest.approx(17.5, abs=1)
    assert reverse.length_frames == forward.length_frames
    # the reverse half still starts exactly where the (now shorter) forward half ends
    assert reverse.start_frame == forward.start_frame + forward.length_frames


def test_boomerang_trim_hold_can_be_turned_off(tmp_path: Path, assets_dir: Path):
    path = _write_yaml(tmp_path, _held_gif_boomerang_yaml(assets_dir, ", boomerang_trim_hold: false"))
    forward, reverse = load_and_resolve(path).tracks[0].placements
    assert forward.length_frames == 25  # the full 1.0s window, untouched
    assert reverse.start_frame == forward.start_frame + forward.length_frames


def test_boomerang_trim_hold_leaves_moving_footage_alone(tmp_path: Path, assets_dir: Path):
    # moving.mp4 is testsrc2: it changes every frame, so there's no hold to trim
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "moving.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 0, source_out: 1, boomerang: true}}
"""
    forward, _ = load_and_resolve(_write_yaml(tmp_path, yaml_text)).tracks[0].placements
    assert forward.length_frames == 25


def test_boomerang_trim_hold_invalid_type_rejected(tmp_path: Path, assets_dir: Path):
    path = _write_yaml(tmp_path, _held_gif_boomerang_yaml(assets_dir, ", boomerang_trim_hold: maybe"))
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "boomerang_trim_hold" in str(exc.value)


def test_boomerang_reverse_uses_same_speed_as_forward_when_unset(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 0, source_out: 1, boomerang: true}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    forward, reverse = resolved.tracks[0].placements
    assert forward.speed is None
    assert reverse.speed is None


def test_boomerang_preserves_crop_on_both_halves(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 0, source_out: 1, boomerang: true, crop: {{top: 10, bottom: 5}}}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    resolved = load_and_resolve(path)
    forward, reverse = resolved.tracks[0].placements
    assert forward.crop == reverse.crop == (10, 0, 5, 0)


def test_boomerang_requires_explicit_duration(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, boomerang: true}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "boomerang" in str(exc.value)


def test_boomerang_conflicts_with_speed_ramp(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, out: 1, boomerang: true,
            speed_ramp: [{{at: 0, speed: 2.0}}]}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "boomerang" in str(exc.value) and "speed_ramp" in str(exc.value)


def test_boomerang_invalid_type_rejected(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, out: 1, boomerang: "yes"}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "boomerang" in str(exc.value)


def test_boomerang_reverse_derivative_is_cached(tmp_path: Path, assets_dir: Path):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 0, source_out: 1, boomerang: true}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    first = load_and_resolve(path)
    second = load_and_resolve(path)
    _, reverse_first = first.tracks[0].placements
    _, reverse_second = second.tracks[0].placements
    # same window -> same cache key -> reused, not regenerated
    assert reverse_first.bin_clip.path == reverse_second.bin_clip.path


def test_boomerang_reverse_derivative_duration_survives_awkward_speed_rounding(
    tmp_path: Path, assets_dir: Path
):
    # Regression test for a real bug found wiring this up against real-world clips
    # (a clip with source_out=2.2s @ speed=2.8688, fps=30): the reverse half's synthetic
    # bin clip duration is derived through the SAME `round(duration_frames / speed)` formula
    # every other speed-warped clip's `out` bound check uses, and for this specific
    # segment/speed/fps combination an earlier version of the padding math rounded the
    # available-duration bound to just *under* what the reverse placement itself needs,
    # failing "'out' exceeds the actual duration" for a derivative file that's actually
    # exactly the right length. Uses the project's own fps (25 here, matching the other
    # fixtures) with a speed picked to land near a frame-rounding boundary the same way.
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, source_in: 0, source_out: 2.2, speed: 2.8688, boomerang: true}}
"""
    path = _write_yaml(tmp_path, yaml_text)
    load_and_resolve(path)  # must not raise ValidationErrors
