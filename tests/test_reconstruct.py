"""Tests for `reconstruct.py`'s core matching functions: given an
already-rendered video (no edit metadata) and a folder of candidate
assets, can we identify which asset a segment came from, and where in a
longer asset it was trimmed from? Uses real `melt` renders as ground
truth, the same way `tests/test_melt_validation.py` does.

Skipped entirely if the `reconstruct` extra (numpy, Pillow) isn't
installed — this is optional functionality, not part of the base
`kdgen build`/`validate`/`import` path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from kdenlive_generator import reconstruct, xmlgen  # noqa: E402
from kdenlive_generator.schema import load_and_resolve  # noqa: E402

from test_melt_validation import _assert_no_fatal_load_error, _run_melt  # noqa: E402


def _render(tmp_path: Path, yaml_text: str, melt, out_name: str = "rendered.mp4") -> Path:
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / out_name
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0
    return rendered


def test_identify_visual_asset_from_render(tmp_path: Path, assets_dir: Path, melt):
    """A render with a still image (0-1.5s) followed by a moving clip
    (1.5-3.5s): each half should be identified as its own source asset, and
    an unused distractor image (photo2.png, from the assets_dir fixture)
    should never win."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5}}
        - {{clip: clip, start: 1.5, duration: 2}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    bank = reconstruct.build_visual_bank(assets_dir, tmp_path)
    assert set(bank) >= {"photo.png", "clip.mp4", "photo2.png"}

    full_signatures = reconstruct.extract_frame_signatures(rendered, tmp_path, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
    midpoint = len(full_signatures) // 2
    first_half, second_half = full_signatures[:midpoint], full_signatures[midpoint:]

    photo_match = reconstruct.identify_visual_asset(first_half, bank)
    clip_match = reconstruct.identify_visual_asset(second_half, bank)

    assert photo_match is not None and photo_match.asset_name == "photo.png"
    assert clip_match is not None and clip_match.asset_name == "clip.mp4"


def test_identify_audio_asset_from_render(tmp_path: Path, assets_dir: Path, melt):
    """The rendered audio track should match the real source tone, not an
    unrelated distractor tone at a different frequency."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3}}
"""
    rendered = _render(tmp_path, yaml_text, melt, out_name="rendered.wav")

    distractor = tmp_path / "distractor_tone.mp3"
    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=3", str(distractor)],
        check=True,
        timeout=30,
    )

    bank_dir = tmp_path / "audio_bank"
    bank_dir.mkdir()
    (bank_dir / "tone.mp3").write_bytes((assets_dir / "tone.mp3").read_bytes())
    (bank_dir / "distractor_tone.mp3").write_bytes(distractor.read_bytes())

    bank = reconstruct.build_audio_bank(bank_dir, tmp_path)
    assert set(bank) == {"tone.mp3", "distractor_tone.mp3"}

    segment_waveform = reconstruct.extract_audio_waveform(rendered, tmp_path)
    match = reconstruct.identify_audio_asset(segment_waveform, bank)

    assert match is not None
    assert match.asset_name == "tone.mp3"
    # A fixed-frequency sine is a poor signal for testing correlation
    # *strength* specifically (it's periodic/self-similar, so the score is
    # noisier and the recovered offset can land on the wrong period - both
    # expected here, same category of caveat as flat colors being a poor
    # signal for aHash). What matters is that it clearly outscores the
    # unrelated distractor, not an absolute magic threshold.
    distractor_score = reconstruct.identify_audio_asset(segment_waveform, {"distractor_tone.mp3": bank["distractor_tone.mp3"]})
    assert distractor_score is not None
    assert match.score > distractor_score.score + 0.3, (
        f"expected tone.mp3 (score={match.score}) to clearly beat the "
        f"distractor (score={distractor_score.score})"
    )


def test_find_trim_offset(tmp_path: Path, assets_dir: Path, melt):
    """Trims the middle of a longer, per-frame-unique source clip (`in`
    nonzero) and checks the recovered start frame against the known offset.
    Regression coverage for the same category of bug fixed in xmlgen.py
    (mltframework/mlt#1282): the trim recovery approach itself already
    depends on frame-accurate rendering, so this doubles as an end-to-end
    check that nonzero `in:` renders correctly."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, in: 1.0, out: 2.5}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    fine_fps = 25
    segment_signatures = reconstruct.extract_frame_signatures(rendered, tmp_path, fps=fine_fps)
    source_signatures = reconstruct.extract_frame_signatures(assets_dir / "clip.mp4", tmp_path, fps=fine_fps)

    result = reconstruct.find_trim_offset(segment_signatures, source_signatures)

    assert result is not None
    start_frame, _mean_distance = result
    expected_start = round(1.0 * fine_fps)
    assert abs(start_frame - expected_start) <= 1, (
        f"expected trim recovery near frame {expected_start} (in=1.0s @ {fine_fps}fps), got {start_frame}"
    )


def test_segment_by_scene_cuts(tmp_path: Path, assets_dir: Path, melt):
    """A three-clip render (still, moving, still) should split into three
    segments with boundaries at roughly the right timestamps."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: clip
    path: {assets_dir / "clip.mp4"}
  - id: photo2
    path: {assets_dir / "photo2.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
        - {{clip: clip, start: 1, duration: 2}}
        - {{clip: photo2, start: 3, duration: 1}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    signatures = reconstruct.extract_frame_signatures(rendered, tmp_path, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
    segments = reconstruct.segment_by_scene_cuts(signatures)

    assert len(segments) == 3, f"expected 3 segments, got {[ (s.start_s, s.end_s) for s in segments ]}"

    tolerance = reconstruct.COARSE_SAMPLE_INTERVAL * 2
    assert abs(segments[0].start_s - 0.0) <= tolerance
    assert abs(segments[0].end_s - 1.0) <= tolerance
    assert abs(segments[1].end_s - 3.0) <= tolerance
    assert abs(segments[2].end_s - 4.0) <= tolerance


def test_detect_video_fade(tmp_path: Path, assets_dir: Path, melt):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2, fade_in: 0.5, fade_out: 0.5}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    fine_fps = 25
    signatures = reconstruct.extract_frame_signatures(rendered, tmp_path, fps=fine_fps)
    interval = 1 / fine_fps

    fade_in = reconstruct.detect_video_fade(signatures, interval)
    fade_out = reconstruct.detect_video_fade(signatures, interval, at_end=True)

    assert abs(fade_in - 0.5) <= interval * 2, f"expected fade_in ~0.5s, got {fade_in}"
    assert abs(fade_out - 0.5) <= interval * 2, f"expected fade_out ~0.5s, got {fade_out}"


def test_detect_audio_fade(tmp_path: Path, assets_dir: Path, melt):
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_in: 1.0}}
"""
    rendered = _render(tmp_path, yaml_text, melt, out_name="rendered.wav")

    waveform = reconstruct.extract_audio_waveform(rendered, tmp_path)
    window_s = 0.15
    fade_in = reconstruct.detect_audio_fade(waveform, reconstruct.AUDIO_SAMPLE_RATE, window_s=window_s)

    assert abs(fade_in - 1.0) <= window_s * 3, f"expected fade_in ~1.0s, got {fade_in}"
