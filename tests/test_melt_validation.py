"""Validation against a real `melt` (MLT engine) binary: if the binary isn't
available on this system, these tests are skipped (see the `melt` fixture in
conftest.py) instead of failing — they're an extra confidence check, not a
requirement for generating projects.

We deliberately don't use `-consumer null`: in this sandboxed dev
environment it hangs indefinitely, even on a trivial built-in producer with
nothing of ours involved (`melt color:red -consumer null`). `-consumer
avformat:<file>` renders for real (actual frame decode/encode, audio muxed
in) and returns promptly, so we use that instead — it also lets us verify
the render's actual duration, not just that melt accepted the file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kdenlive_generator import probe, xmlgen
from kdenlive_generator.models import ResolvedProject
from kdenlive_generator.schema import load_and_resolve


def _project_yaml(assets_dir: Path) -> str:
    return f"""
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
        - {{clip: tone, start: 0, out: 3, fade_out: 0.5}}
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5, fade_in: 0.5}}
        - {{clip: clip, start: 1.5, duration: 2}}
"""


def _build(tmp_path: Path, assets_dir: Path) -> tuple[Path, ResolvedProject]:
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(_project_yaml(assets_dir), encoding="utf-8")
    resolved = load_and_resolve(yaml_path)
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, out_path)
    return out_path, resolved


def _run_melt(melt, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    exe, extra_env = melt
    env = {**os.environ, **extra_env}
    return subprocess.run([exe, *args], capture_output=True, text=True, env=env, timeout=timeout)


def _non_fatal_stderr(result: subprocess.CompletedProcess) -> list[str]:
    return [
        line
        for line in result.stderr.splitlines()
        # the test runner invokes the snap's melt-7 "raw", outside its
        # confinement, so it's missing the snap's own Qt6 and can't load the
        # "qtblend" service (see README); inside real kdenlive that library
        # is available. This isn't a failure of the generated XML.
        if "qtblend" not in line and "libmltqt6" not in line
    ]


def _assert_no_fatal_load_error(result: subprocess.CompletedProcess) -> list[str]:
    stderr_lines = _non_fatal_stderr(result)
    fatal = [l for l in stderr_lines if "Failed to load" in l and "project.kdenlive" in l]
    assert not fatal, "melt failed to load the generated project:\n" + "\n".join(stderr_lines)
    return stderr_lines


def test_melt_can_load_generated_project(tmp_path: Path, assets_dir: Path, melt):
    out_path, _resolved = _build(tmp_path, assets_dir)
    xml_out = tmp_path / "roundtrip.xml"

    result = _run_melt(melt, [str(out_path), "-consumer", f"xml:{xml_out}", "all_frames=0"])

    _assert_no_fatal_load_error(result)
    assert xml_out.exists() and xml_out.stat().st_size > 0


def test_melt_can_render_generated_project(tmp_path: Path, assets_dir: Path, melt):
    """Stronger than the structural check above: actually decodes and
    re-encodes every frame instead of just dumping the parsed structure, and
    confirms the rendered output's duration matches what kdgen computed —
    catching cases where melt loads a project fine but silently misreads a
    property (wrong duration, dropped clip) that a structural dump alone
    wouldn't reveal."""
    if not probe.ffprobe_available():
        pytest.skip("ffprobe not available: can't inspect the rendered output")

    out_path, resolved = _build(tmp_path, assets_dir)
    rendered = tmp_path / "rendered.mp4"

    result = _run_melt(melt, [str(out_path), "-consumer", f"avformat:{rendered}"])

    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    probed = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(rendered),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    rendered_duration = float(probed.stdout.strip())
    expected_duration = resolved.fps.frames_to_seconds(resolved.total_frames)
    frame = 1 / resolved.fps.fps
    assert abs(rendered_duration - expected_duration) <= frame, (
        f"rendered duration {rendered_duration}s doesn't match the "
        f"{expected_duration}s kdgen computed ({resolved.total_frames} frames @ {resolved.fps.fps}fps)"
    )


def _mean_volume_db(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner",
            "-i", str(path),
            "-ss", str(start), "-t", str(duration),
            "-af", "volumedetect",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].strip().split(" ")[0])
    raise RuntimeError(f"could not parse mean_volume from ffmpeg output:\n{result.stderr}")


def test_melt_renders_a_real_audio_fade(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check, not just structural: renders a fade_in and confirms
    the audio actually gets louder across the fade span, instead of only
    asserting the filter XML looks plausible. This is what "audio fades are
    unverified" (see BACKLOG.md, now resolved) was about — the gain/end
    property names and kdenlive_id were previously guessed by analogy with
    the video fade filter and didn't match kdenlive's real "fadein"/
    "fadeout" audio effects (see xmlgen.py's _add_audio_fade_filters)."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_in: 1.5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.wav"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    quiet = _mean_volume_db(rendered, start=0.0, duration=0.15)
    mid = _mean_volume_db(rendered, start=0.6, duration=0.15)
    loud = _mean_volume_db(rendered, start=2.0, duration=0.15)

    assert quiet < mid < loud, (
        f"expected volume to ramp up across the fade_in, got "
        f"quiet={quiet}dB mid={mid}dB loud={loud}dB"
    )
