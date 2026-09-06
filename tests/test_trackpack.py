"""Tests for `trackpack.pack_tracks` (the `build --pack-tracks` CLI flag):
repacking a resolved project's tracks onto the fewest possible, without
changing what gets rendered."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_as_code import cli, xmlgen
from kdenlive_as_code.schema import load_and_resolve
from kdenlive_as_code.trackpack import pack_tracks


def _resolve(tmp_path: Path, yaml_text: str):
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    return load_and_resolve(yaml_path)


def test_non_overlapping_clips_merge_onto_one_track(tmp_path: Path, assets_dir: Path):
    """Three photos, one per track, none overlapping (no fades) - the
    textbook case this flag exists for: they should all end up on a single
    video track, in their original order."""
    resolved = _resolve(
        tmp_path,
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
    - type: video
      clips:
        - {{clip: photo, start: 1, duration: 1}}
    - type: video
      clips:
        - {{clip: photo, start: 2, duration: 1}}
""",
    )
    assert len(resolved.tracks) == 3

    packed = pack_tracks(resolved)

    video_tracks = [t for t in packed.tracks if t.kind == "video"]
    assert len(video_tracks) == 1
    assert [p.start_frame for p in video_tracks[0].placements] == [0, 25, 50]


def test_overlapping_crossfade_clips_keep_their_own_tracks(tmp_path: Path, assets_dir: Path):
    """Two clips that overlap in time (a crossfade hand-off: the first has
    no fade and stays opaque, the second fades in over it - the pattern
    this project's own timeline uses, see xmlgen.py's `_add_video_fade_
    filters` docstring) can't share a track without breaking the
    compositing order, so packing must leave them on two tracks."""
    resolved = _resolve(
        tmp_path,
        f"""
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
        - {{clip: photo, start: 0, duration: 2}}
    - type: video
      clips:
        - {{clip: photo2, start: 1.5, duration: 2, fade_in: 0.5}}
""",
    )

    packed = pack_tracks(resolved)

    video_tracks = [t for t in packed.tracks if t.kind == "video"]
    assert len(video_tracks) == 2
    assert len(video_tracks[0].placements) == 1
    assert len(video_tracks[1].placements) == 1


def test_third_clip_after_the_crossfade_reuses_the_freed_track(tmp_path: Path, assets_dir: Path):
    """A third clip that starts *after* the first one has already ended
    (no longer active) and doesn't itself overlap anything on that track
    can safely reuse it, even though the whole thing is one continuous
    hand-off chain."""
    resolved = _resolve(
        tmp_path,
        f"""
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
        - {{clip: photo, start: 0, duration: 2}}
    - type: video
      clips:
        - {{clip: photo2, start: 1.5, duration: 2, fade_in: 0.5}}
    - type: video
      clips:
        - {{clip: photo, start: 5, duration: 1}}
""",
    )
    assert len(resolved.tracks) == 3

    packed = pack_tracks(resolved)

    video_tracks = [t for t in packed.tracks if t.kind == "video"]
    assert len(video_tracks) == 2
    counts = sorted(len(t.placements) for t in video_tracks)
    assert counts == [1, 2]


def test_stacking_order_follows_declaration_not_start_time(tmp_path: Path, assets_dir: Path):
    """A track declared *first* but that starts *later* (like this
    project's own `intro_audio`/`header` pair) must still end up below a
    track declared after it that starts earlier and overlaps it - "on top"
    is decided by declaration order, not which one the clock reaches
    first. Regression test for a real bug: an earlier version of this
    module sorted placements by start time before assigning tracks, which
    silently inverted this exact pair when packed."""
    resolved = _resolve(
        tmp_path,
        f"""
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
        - {{clip: photo, start: 0.5, duration: 3}}
    - type: video
      clips:
        - {{clip: photo2, start: 0, duration: 1}}
""",
    )

    packed = pack_tracks(resolved)

    video_tracks = [t for t in packed.tracks if t.kind == "video"]
    assert len(video_tracks) == 2
    # `photo` (declared first) must stay below `photo2` (declared second,
    # even though it starts earlier and finishes first).
    assert video_tracks[0].placements[0].bin_clip.id == "photo"
    assert video_tracks[1].placements[0].bin_clip.id == "photo2"


def test_pack_tracks_is_idempotent_on_placement_content(tmp_path: Path, assets_dir: Path):
    """Repacking shouldn't drop, duplicate, or reorder-within-track any
    placement - every original one must show up exactly once."""
    resolved = _resolve(
        tmp_path,
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 1}}
    - type: audio
      clips:
        - {{clip: tone, start: 2, out: 1}}
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
    - type: video
      clips:
        - {{clip: photo, start: 1.5, duration: 1, fade_in: 0.3}}
""",
    )
    before = sorted(p.start_frame for t in resolved.tracks for p in t.placements)

    packed = pack_tracks(resolved)

    after = sorted(p.start_frame for t in packed.tracks for p in t.placements)
    assert before == after
    assert sum(len(t.placements) for t in packed.tracks) == sum(len(t.placements) for t in resolved.tracks)


def test_track_referenced_by_a_wipe_transition_is_left_alone(tmp_path: Path, assets_dir: Path):
    """A track named in a `wipe`/`affine` transition can't be silently
    merged into another one without breaking that reference (xmlgen.py
    looks the transition's `track_a`/`track_b` up by name) - it must keep
    its own dedicated output track."""
    resolved = _resolve(
        tmp_path,
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: photo2
    path: {assets_dir / "photo2.png"}
scenes:
  - scene: s1
    transition: wipe
    transition_duration: 0.5
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 3.0
""",
    )
    n_before = len(resolved.tracks)
    assert len(resolved.wipe_transitions) == 1

    packed = pack_tracks(resolved)

    assert len(packed.tracks) == n_before
    packed_names = {t.name for t in packed.tracks}
    for w in resolved.wipe_transitions:
        assert w.track_a in packed_names
        assert w.track_b in packed_names


def _mean_rgb(path: Path, timestamp: float) -> tuple[float, float, float]:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=30, check=True,
    )
    data = result.stdout
    n = len(data) // 3
    return (sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n)


def test_packed_render_still_crossfades_correctly(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical, real-render check: two independent red->blue crossfade
    pairs, far enough apart in time that the second pair doesn't overlap
    the first at all - packing should reuse both of the first pair's
    tracks for the second (4 clips onto 2 tracks) *and* every transition
    must still render the correct blended color, not just land on the
    right track count."""
    resolved = _resolve(
        tmp_path,
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: red
    path: {assets_dir / "photo.png"}
  - id: blue
    path: {assets_dir / "photo2.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: red, start: 0, duration: 2}}
    - type: video
      clips:
        - {{clip: blue, start: 1.5, duration: 2, fade_in: 0.5}}
    - type: video
      clips:
        - {{clip: red, start: 6, duration: 2}}
    - type: video
      clips:
        - {{clip: blue, start: 7.5, duration: 2, fade_in: 0.5}}
""",
    )
    packed = pack_tracks(resolved)
    video_tracks = [t for t in packed.tracks if t.kind == "video"]
    assert len(video_tracks) == 2  # 4 clips packed onto 2 tracks, each pair reusing the other's

    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(packed, kdenlive_path)
    rendered = tmp_path / "rendered.mp4"
    exe, extra_env = melt_with_qtblend
    import os
    env = {**os.environ, **extra_env}
    result = subprocess.run(
        [exe, str(kdenlive_path), "-consumer", f"avformat:{rendered}"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert "Failed to load" not in result.stderr
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction(t: float) -> float:
        r, _g, b = _mean_rgb(rendered, t)
        return r / (r + b + 1e-6)

    assert red_fraction(0.5) > 0.9  # first pair: solid red, before its transition
    mid_first_transition = red_fraction(1.75)
    assert 0.15 < mid_first_transition < 0.85, (
        f"expected a genuine red->blue blend in the first transition, got red_fraction={mid_first_transition}"
    )
    assert red_fraction(3.0) < 0.1  # first pair: solid blue after its transition
    assert red_fraction(6.5) > 0.9  # second pair: solid red, reusing the first pair's freed track
    mid_second_transition = red_fraction(7.75)
    assert 0.15 < mid_second_transition < 0.85, (
        f"expected a genuine red->blue blend in the second transition, got red_fraction={mid_second_transition}"
    )
    assert red_fraction(8.75) < 0.1  # second pair: solid blue after its transition


def test_cli_build_pack_tracks_flag(tmp_path: Path, assets_dir: Path, capsys):
    """`kdenlive-as-code build --pack-tracks` should produce a .kdenlive
    with fewer tracks than a plain `build` of the same project, and its
    success message should report the (now lower) track count."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
    - type: video
      clips:
        - {{clip: photo, start: 1, duration: 1}}
""",
        encoding="utf-8",
    )

    plain_out = tmp_path / "plain.kdenlive"
    rc = cli.main(["build", str(yaml_path), "-o", str(plain_out)])
    assert rc == 0
    capsys.readouterr()

    packed_out = tmp_path / "packed.kdenlive"
    rc = cli.main(["build", str(yaml_path), "-o", str(packed_out), "--pack-tracks"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 track" in out or "1 pista" in out

    def n_content_tracks(path: Path) -> int:
        root = ET.parse(path).getroot()
        sequence = next(t for t in root.findall("tractor") if t.find("./property[@name='kdenlive:uuid']") is not None)
        return len(sequence.findall("track")) - 1  # minus the black background track

    assert n_content_tracks(plain_out) == 2
    assert n_content_tracks(packed_out) == 1
