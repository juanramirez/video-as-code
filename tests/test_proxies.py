"""proxies.py: `--proxy` light copies of only the used source ranges."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from video_as_code import cli, derive, proxies
from video_as_code.schema import load_and_resolve


@pytest.fixture(scope="module")
def long_video(tmp_path_factory) -> Path:
    """20 s of really moving footage (every frame differs), 25 fps."""
    d = tmp_path_factory.mktemp("long")
    path = d / "long.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=20",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _project(tmp_path: Path, bin_lines: str, clip_lines: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
{bin_lines}
timeline:
  tracks:
    - type: video
      clips:
{clip_lines}
""",
        encoding="utf-8",
    )
    return path


def _frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", "scale=32:24", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).astype(float)


def _placements(resolved):
    return [p for t in resolved.tracks for p in t.placements]


def test_clusters_merge_near_ranges_split_far_ones_and_pad():
    spans = proxies._clusters([(2.0, 4.0), (5.0, 6.0), (15.0, 16.0)], duration_s=16.5)
    assert spans == [(1.0, 7.0), (14.0, 16.5)]  # padded by MARGIN_S, clamped to the file's end


def test_ramp_source_seconds():
    # 25 frames at x2 then 50 at x0.5 = 2 s + 1 s of source
    assert proxies._ramp_source_seconds([(0, 2.0), (25, 0.5)], 75, 25.0) == pytest.approx(3.0)


@pytest.mark.parametrize("text", ["480", "0x270", "481x270", "axb"])
def test_parse_size_rejects(text: str):
    with pytest.raises(ValueError):
        proxies.parse_size(text)


def test_proxies_copy_only_the_used_ranges(tmp_path: Path, long_video: Path, assets_dir: Path):
    project = _project(
        tmp_path,
        f"  - {{id: v, path: {long_video}}}\n  - {{id: still, path: {assets_dir / 'photo.png'}}}",
        "        - {clip: v, start: 0, source_in: 2, source_out: 4}\n"
        "        - {clip: still, start: 2, duration: 1}\n"
        "        - {clip: v, start: 3, source_in: 15, source_out: 16, speed: 2}",
    )
    original = load_and_resolve(project)
    resolved = proxies.apply(original, (160, 120), errors := [])
    assert errors == []
    first, still, fast = _placements(resolved)
    assert still.bin_clip.path == assets_dir / "photo.png"  # stills are left alone
    assert first.bin_clip.path.parent == fast.bin_clip.path.parent == tmp_path / proxies.PROXY_DIR
    assert first.bin_clip.path != fast.bin_clip.path  # 11 s apart: one copy each
    # the copies start MARGIN_S before what's used: 1.0 s and 14.0 s into the original
    assert (first.in_frame, first.out_frame) == (25, 74)
    # x2: shifted by the copy's start (in its name) in the placement's own warped frames - so the
    # same source second is read, give or take half a warped frame (speed / fps / 2 = 0.04 s)
    start = float(fast.bin_clip.path.name.split("~")[1].rsplit("-", 1)[0])
    assert 13.9 < start < 14.1
    read = fast.in_frame / 25 * 2 + start
    assert read == pytest.approx(_placements(original)[2].in_frame / 25 * 2, abs=0.04 + 1e-9)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(first.bin_clip.path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert probe == "160,120"
    # the copy's picture at its own 1.0 s is the original's at 2.0 s
    diff = np.abs(_frame(first.bin_clip.path, 1.0) - _frame(long_video, 2.0)).mean()
    other = np.abs(_frame(first.bin_clip.path, 1.0) - _frame(long_video, 3.0)).mean()
    assert diff < 8 < other
    # the bin entry itself points at a copy too, so no full-size producer is loaded
    assert {c.id: c.path for c in resolved.bin_clips}["v"] == first.bin_clip.path


def test_proxies_are_cached(tmp_path: Path, long_video: Path, monkeypatch):
    project = _project(tmp_path, f"  - {{id: v, path: {long_video}}}", "        - {clip: v, start: 0, source_in: 2, source_out: 4}")
    proxies.apply(load_and_resolve(project), (160, 120), [])

    def no_generation(*args, **kwargs):
        raise AssertionError("regenerated a current copy")

    monkeypatch.setattr(derive, "generate", no_generation)
    proxies.apply(load_and_resolve(project), (160, 120), errors := [])
    assert errors == []


def test_proxies_shift_a_speed_ramps_source_offset(tmp_path: Path, long_video: Path):
    project = _project(
        tmp_path,
        f"  - {{id: v, path: {long_video}}}",
        "        - {clip: v, start: 0, source_in: 10, duration: 2, speed_ramp: [{at: 0, speed: 2}, {at: 1, speed: 1}]}",
    )
    (ramp,) = _placements(proxies.apply(load_and_resolve(project), (160, 120), []))
    assert ramp.speed_ramp_source_offset_s == pytest.approx(1.0)  # the copy starts at 9.0 s


def test_unused_bin_video_gets_a_tiny_copy(tmp_path: Path, long_video: Path, assets_dir: Path):
    project = _project(
        tmp_path,
        f"  - {{id: v, path: {long_video}}}\n  - {{id: still, path: {assets_dir / 'photo.png'}}}",
        "        - {clip: still, start: 0, duration: 1}",
    )
    resolved = proxies.apply(load_and_resolve(project), (160, 120), [])
    (copy,) = [c for c in resolved.bin_clips if c.id == "v"]
    assert copy.path.parent == tmp_path / proxies.PROXY_DIR
    assert copy.duration_frames <= round((proxies.UNUSED_S + proxies.MARGIN_S) * 25)


def test_cli_build_with_proxy(tmp_path: Path, long_video: Path, capsys):
    project = _project(tmp_path, f"  - {{id: v, path: {long_video}}}", "        - {clip: v, start: 0, source_in: 2, source_out: 4}")
    assert cli.main(["build", "--proxy", "160x120", str(project)]) == 0
    assert f"{proxies.PROXY_DIR}/v~1-" in (tmp_path / "project.kdenlive").read_text()
    assert cli.main(["build", "--proxy", "160", str(project)]) == 1
    assert "--proxy" in capsys.readouterr().err
