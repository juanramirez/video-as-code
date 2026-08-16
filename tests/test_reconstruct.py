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

import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from kdenlive_generator import reconstruct, xmlgen  # noqa: E402
from kdenlive_generator.schema import load_and_resolve  # noqa: E402

from test_melt_validation import _assert_no_fatal_load_error, _run_melt  # noqa: E402

PUBLIC_DOMAIN_DIR = Path(__file__).parent / "fixtures" / "public_domain"


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

    bank = reconstruct.build_visual_bank(assets_dir)
    assert set(bank) >= {"photo.png", "clip.mp4", "photo2.png"}

    full_signatures = reconstruct.extract_frame_signatures(rendered, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
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


def test_build_audio_bank_same_stem_different_extension(tmp_path: Path):
    """`build_audio_bank` now processes assets concurrently (a thread pool,
    not a sequential loop - see reconstruct.py's rationale on
    `build_visual_bank`); two assets that differ only by extension (e.g.
    `song.mp3` next to `song.wav`) used to target the same
    `audio_{stem}.wav` temp path, safe only because the old sequential
    loop always fully read one before starting the next. Concurrently,
    that's a real race - regression test for the `path.name`-based (not
    `path.stem`-based) temp filename fix: each asset's own distinct
    (different-frequency) tone should still come back as its own,
    uncorrupted waveform."""
    bank_dir = tmp_path / "bank"
    bank_dir.mkdir()
    common = ["-y", "-loglevel", "error"]
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(bank_dir / "song.mp3")],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "sine=frequency=880:duration=2", str(bank_dir / "song.wav")],
        check=True,
    )

    bank = reconstruct.build_audio_bank(bank_dir, tmp_path)
    assert set(bank) == {"song.mp3", "song.wav"}

    match_mp3 = reconstruct.identify_audio_asset(bank["song.mp3"].waveform, {"song.mp3": bank["song.mp3"], "song.wav": bank["song.wav"]})
    match_wav = reconstruct.identify_audio_asset(bank["song.wav"].waveform, {"song.mp3": bank["song.mp3"], "song.wav": bank["song.wav"]})
    assert match_mp3 is not None and match_mp3.asset_name == "song.mp3", (
        "song.mp3's own waveform should match itself, not have been overwritten by song.wav's ffmpeg output"
    )
    assert match_wav is not None and match_wav.asset_name == "song.wav"


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
    segment_signatures = reconstruct.extract_frame_signatures(rendered, fps=fine_fps)
    source_signatures = reconstruct.extract_frame_signatures(assets_dir / "clip.mp4", fps=fine_fps)

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

    signatures = reconstruct.extract_frame_signatures(rendered, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
    segments = reconstruct.segment_by_scene_cuts(signatures)

    assert len(segments) == 3, f"expected 3 segments, got {[ (s.start_s, s.end_s) for s in segments ]}"

    tolerance = reconstruct.COARSE_SAMPLE_INTERVAL * 2
    assert abs(segments[0].start_s - 0.0) <= tolerance
    assert abs(segments[0].end_s - 1.0) <= tolerance
    assert abs(segments[1].end_s - 3.0) <= tolerance
    assert abs(segments[2].end_s - 4.0) <= tolerance


def test_segment_photo_to_photo(tmp_path: Path, assets_dir: Path, melt):
    """The one asset-type transition test_segment_by_scene_cuts doesn't
    cover: it goes photo->video->photo, never a direct photo->photo cut
    with no video in between."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: photo2
    path: {assets_dir / "photo2.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
        - {{clip: photo2, start: 1, duration: 1}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    signatures = reconstruct.extract_frame_signatures(rendered, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
    segments = reconstruct.segment_by_scene_cuts(signatures)

    assert len(segments) == 2, f"expected 2 segments, got {[(s.start_s, s.end_s) for s in segments]}"
    tolerance = reconstruct.COARSE_SAMPLE_INTERVAL * 2
    assert abs(segments[0].end_s - 1.0) <= tolerance
    assert abs(segments[1].end_s - 2.0) <= tolerance

    bank = reconstruct.build_visual_bank(assets_dir)
    midpoint = len(signatures) // 2
    first = reconstruct.identify_visual_asset(signatures[:midpoint], bank)
    second = reconstruct.identify_visual_asset(signatures[midpoint:], bank)

    assert first is not None and first.asset_name == "photo.png"
    assert second is not None and second.asset_name == "photo2.png"


def test_segment_video_to_video(tmp_path: Path, assets_dir: Path, melt):
    """The other asset-type transition test_segment_by_scene_cuts doesn't
    cover: two different video clips back to back, no still image in
    between. Uses the real calcutta.mp4 fixture alongside a synthetic clip
    so the two video sources are genuinely distinguishable content, not
    just two different-looking patterns.

    The synthetic clip is generated at calcutta.mp4's own resolution
    (320x180), not the shared `assets_dir` fixture's clip.mp4 (320x240):
    this test's sandboxed `melt` can't load the `qtblend` service (see
    `test_melt_validation.py`), so a project whose dimensions don't match
    a bin clip's native resolution renders that clip uncomposited (blank)
    instead of scaled - a test-environment limitation, not something
    reconstruct.py's matching needs to tolerate."""
    calcutta = PUBLIC_DOMAIN_DIR / "calcutta.mp4"
    clip = tmp_path / "clip180.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=3",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 180}}
bin:
  - id: clip
    path: {clip}
  - id: calcutta
    path: {calcutta}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, duration: 1.5}}
        - {{clip: calcutta, start: 1.5, duration: 1.5}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    signatures = reconstruct.extract_frame_signatures(rendered, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
    segments = reconstruct.segment_by_scene_cuts(signatures)

    assert len(segments) == 2, f"expected 2 segments, got {[(s.start_s, s.end_s) for s in segments]}"
    tolerance = reconstruct.COARSE_SAMPLE_INTERVAL * 2
    assert abs(segments[0].end_s - 1.5) <= tolerance
    assert abs(segments[1].end_s - 3.0) <= tolerance

    bank_dir = tmp_path / "bank"
    bank_dir.mkdir()
    (bank_dir / "clip.mp4").write_bytes(clip.read_bytes())
    (bank_dir / "calcutta.mp4").write_bytes(calcutta.read_bytes())
    bank = reconstruct.build_visual_bank(bank_dir)

    midpoint = len(signatures) // 2
    first = reconstruct.identify_visual_asset(signatures[:midpoint], bank)
    second = reconstruct.identify_visual_asset(signatures[midpoint:], bank)

    assert first is not None and first.asset_name == "clip.mp4"
    assert second is not None and second.asset_name == "calcutta.mp4"


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
    signatures = reconstruct.extract_frame_signatures(rendered, fps=fine_fps)
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


def test_reconstruct_end_to_end_round_trip(tmp_path: Path, assets_dir: Path, melt):
    """The full pipeline: build a known project, render it, reconstruct a
    YAML from just the render + the assets folder (no access to the
    original YAML), and check the recovered placements against the
    originals. No fades here on purpose - fades change a segment's actual
    pixel/sample content relative to the plain source asset, which the
    matching step (correctly) treats as a real content difference; that
    interaction is a known, documented limitation (see reconstruct.py's
    module docstring / BACKLOG.md), not what this test is checking."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: clip
    path: {assets_dir / "clip.mp4"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3}}
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5}}
        - {{clip: clip, start: 1.5, in: 0.3, out: 2.3}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    result = reconstruct.reconstruct(rendered, assets_dir)

    assert "x_reconstruct_unmatched" not in result.data, (
        f"expected everything to match confidently, got: {result.data.get('x_reconstruct_unmatched')}"
    )

    tracks = {t["type"]: t for t in result.data["timeline"]["tracks"]}
    video_clips = tracks["video"]["clips"]
    audio_clips = tracks["audio"]["clips"]

    assert len(video_clips) == 2
    tol = reconstruct.COARSE_SAMPLE_INTERVAL * 2

    photo_clip, clip_clip = video_clips
    assert abs(photo_clip["start"] - 0.0) <= tol
    assert abs(photo_clip["duration"] - 1.5) <= tol
    assert "in" not in photo_clip  # photo is a still image, no trim to recover

    assert abs(clip_clip["start"] - 1.5) <= tol
    assert abs(clip_clip.get("in", 0.0) - 0.3) <= tol
    assert abs(clip_clip["duration"] - 2.0) <= tol

    assert len(audio_clips) == 1
    assert abs(audio_clips[0]["start"] - 0.0) <= tol
    assert abs(audio_clips[0]["duration"] - 3.0) <= tol

    # The recovered YAML must also be valid against our own schema, not
    # just "look right" - same bar test_reverse.py's round-trip tests hold
    # reverse.py's output to.
    reconstructed_yaml = tmp_path / "reconstructed.yaml"
    import yaml as pyyaml
    reconstructed_yaml.write_text(pyyaml.dump(result.data, sort_keys=False), encoding="utf-8")
    load_and_resolve(reconstructed_yaml)  # must not raise


def test_reconstruct_reports_progress(tmp_path: Path, assets_dir: Path, melt):
    """`on_progress` (new: reconstruct used to run completely silent until
    it finished or crashed, indistinguishable from a hang on real,
    multi-minute footage) gets called repeatedly across the real stages of
    the pipeline, not just once at the start or end."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    messages: list[str] = []
    reconstruct.reconstruct(rendered, assets_dir, on_progress=messages.append)

    assert len(messages) >= 4, f"expected several progress updates, got: {messages}"
    joined = " ".join(messages)
    assert "photo.png" in joined or "rendered.mp4" in joined, (
        f"expected at least one progress message naming a real file, got: {messages}"
    )


def test_run_ffmpeg_timeout_raises_reconstruct_error(monkeypatch):
    """A hung/slow ffmpeg call used to crash with a raw, uncaught
    `subprocess.TimeoutExpired` traceback instead of the clean
    `ReconstructError` every other real failure mode in this module
    raises - caught in the wild on real footage (a full-length render's
    coarse frame extraction legitimately took longer than the old,
    too-short 60s default)."""
    import subprocess

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(reconstruct.ReconstructError):
        reconstruct._run_ffmpeg(["-i", "whatever"], timeout=1)


def test_matches_real_photographic_and_film_content(tmp_path: Path, assets_dir: Path, melt):
    """Everything else in this file uses synthetic content (solid colors,
    ffmpeg testsrc, mandelbrot zooms). This is the one real-world check:
    an actual photograph (a cat, CC0) and an actual archival film clip
    (public domain, real people/architecture/camera motion) - see
    fixtures/public_domain/SOURCES.md for provenance. Confirms matching
    isn't secretly relying on properties only synthetic test content has."""
    cat = PUBLIC_DOMAIN_DIR / "cat.jpg"
    calcutta = PUBLIC_DOMAIN_DIR / "calcutta.mp4"

    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 180}}
bin:
  - id: cat
    path: {cat}
  - id: calcutta
    path: {calcutta}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: cat, start: 0, duration: 1.5}}
        - {{clip: calcutta, start: 1.5, duration: 2.5}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    # A bank with a real distractor too (a solid-color image from the
    # existing synthetic fixtures) - the real content shouldn't get
    # confused with it in either direction.
    bank_dir = tmp_path / "bank"
    bank_dir.mkdir()
    (bank_dir / "cat.jpg").write_bytes(cat.read_bytes())
    (bank_dir / "calcutta.mp4").write_bytes(calcutta.read_bytes())
    (bank_dir / "distractor.png").write_bytes((assets_dir / "photo2.png").read_bytes())

    bank = reconstruct.build_visual_bank(bank_dir)
    assert set(bank) == {"cat.jpg", "calcutta.mp4", "distractor.png"}

    signatures = reconstruct.extract_frame_signatures(rendered, fps=1 / reconstruct.COARSE_SAMPLE_INTERVAL)
    midpoint = len(signatures) // 2
    cat_match = reconstruct.identify_visual_asset(signatures[:midpoint], bank)
    calcutta_match = reconstruct.identify_visual_asset(signatures[midpoint:], bank)

    assert cat_match is not None and cat_match.asset_name == "cat.jpg"
    assert calcutta_match is not None and calcutta_match.asset_name == "calcutta.mp4"


def test_confidence_pct_helpers():
    """0-100, higher-is-better on both sides (new: `x_reconstruct_unmatched`
    entries used to expose two internal numbers with different scales and
    opposite directions - a video match's raw signature-distance (LOWER is
    better, no fixed upper bound) and an audio match's ~0..1 correlation
    score (HIGHER is better) - under the same ambiguous `confidence` key,
    genuinely confusing to read without reading the source)."""
    assert reconstruct._video_confidence_pct(0.0) == 100.0
    assert reconstruct._video_confidence_pct(reconstruct.VIDEO_MATCH_DISTANCE_THRESHOLD) == 0.0
    assert reconstruct._video_confidence_pct(reconstruct.VIDEO_MATCH_DISTANCE_THRESHOLD * 2) == 0.0  # clamped, not negative

    assert reconstruct._audio_confidence_pct(1.0) == 100.0
    assert reconstruct._audio_confidence_pct(0.0) == 0.0
    assert reconstruct._audio_confidence_pct(-0.5) == 0.0  # clamped, not negative


def test_unmatched_segment_reports_confidence_pct(tmp_path: Path, assets_dir: Path, melt):
    """A real unmatched segment's `x_reconstruct_unmatched` entry carries
    `confidence_pct` (0-100, higher is better) instead of the old raw,
    ambiguous `confidence` number."""
    yaml_text = f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5}}
"""
    rendered = _render(tmp_path, yaml_text, melt)

    # photo.png (assets_dir) is red; a bank containing only the unrelated
    # blue photo2.png should never confidently match it.
    wrong_assets = tmp_path / "wrong_assets"
    wrong_assets.mkdir()
    (wrong_assets / "photo2.png").write_bytes((assets_dir / "photo2.png").read_bytes())

    result = reconstruct.reconstruct(rendered, wrong_assets)

    unmatched = result.data.get("x_reconstruct_unmatched")
    assert unmatched, f"expected an unmatched entry, got: {result.data}"
    entry = unmatched[0]
    assert entry["best_guess"] == "photo2.png"
    assert "confidence" not in entry  # old, ambiguous key must be gone
    assert 0.0 <= entry["confidence_pct"] < 100.0
