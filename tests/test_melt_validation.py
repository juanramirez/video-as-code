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


def test_melt_mutes_an_audio_track_clip(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `mute: true` on an audio-track clip: the same
    tone that renders clearly audible without `mute` should render silent
    (or near-digital-silence) with it."""
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
        - {{clip: tone, start: 0, out: 3, mute: true}}
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

    muted = _mean_volume_db(rendered, start=1.0, duration=1.0)
    assert muted < -90, f"expected near-silence with mute: true, got {muted}dB"


def test_melt_mutes_a_video_clips_own_audio(tmp_path: Path, assets_dir: Path, melt):
    """`mute: true` on a video-track clip should silence that clip's own
    embedded audio while leaving the picture untouched - this is what
    lets a video's image and its (separately-placed) audio have
    independent fades: mute the video-track copy, fade the audio-track
    copy (see ROADMAP.md)."""
    # A solid color, not `testsrc` - testsrc's rainbow of hues would average
    # out to a near-neutral grey across the whole frame either way, so it
    # can't tell a rendered picture apart from a blank one via _mean_rgb.
    clip_with_sound = tmp_path / "clip_with_sound.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=25:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-pix_fmt", "yuv420p", "-shortest", str(clip_with_sound),
        ],
        check=True,
    )
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {clip_with_sound}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, mute: true}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    muted = _mean_volume_db(rendered, start=0.5, duration=1.0)
    assert muted < -90, f"expected the video clip's own audio to be silenced, got {muted}dB"

    r, g, b = _mean_rgb(rendered, timestamp=0.5)
    assert r > g + 20 and r > b + 20, f"expected the red picture to still render, got {(r, g, b)}"


def _mean_rgb(path: Path, timestamp: float = 0.0) -> tuple[float, float, float]:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-ss", str(timestamp), "-i", str(path),
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    data = result.stdout
    n = len(data) // 3
    return (sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n)


def test_melt_respects_nonzero_clip_in_point(tmp_path: Path, melt):
    """Regression test for a real upstream MLT behavior: a playlist
    <entry>'s in/out are relative to the producer/chain it references, not
    absolute frame numbers. xmlgen.py used to set the same trim on both the
    chain and the entry, so the effective read position was offset further
    into the source than intended whenever `in` was nonzero (reported as
    mltframework/mlt#1282; closed "not planned" since it's documented
    behavior, not a bug — the chain must carry the full source range and
    only the entry should trim). The exact wrong frame isn't a clean
    formula — it seemed to depend on the specific in/out values and the
    source's keyframes, not a fixed "2x in" in every case — so this uses
    values confirmed to reproduce it reliably against the pre-fix code, not
    just plausible-looking ones. Verified with a real render, not just XML
    structure: builds a red/green/blue three-band source, trims to just the
    middle (green) band, and checks the rendered pixels are actually green
    - not red or blue."""
    # Band width and `in`/`out` matter: a 1s-per-band asset with in=1.0/out=2.0
    # didn't reliably reproduce the bug (whatever wrong offset it read still
    # landed inside the same band, by coincidence of these particular
    # numbers/this asset's keyframes) even though the underlying arithmetic
    # error is real. 2s-per-band with in=2.0/out=4.5 does reproduce it
    # reliably (verified against the pre-fix code) - keep these values.
    bands = tmp_path / "bands.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=red:size=32x32:duration=2:rate=25",
            "-f", "lavfi", "-i", "color=c=green:size=32x32:duration=2:rate=25",
            "-f", "lavfi", "-i", "color=c=blue:size=32x32:duration=2:rate=25",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
            str(bands),
        ],
        check=True,
        timeout=30,
    )

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 32, height: 32}}
bin:
  - id: bands
    path: {bands}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: bands, start: 0, in: 2.0, out: 4.5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    r, g, b = _mean_rgb(rendered, timestamp=0.0)
    assert g > r and g > b, (
        f"expected the middle (green) band from in=2.0, got mean rgb=({r:.0f}, {g:.0f}, {b:.0f})"
    )


def test_melt_renders_a_real_video_fade(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check, not just structural: renders a fade_in and confirms
    the image actually gets brighter across the fade span. Regression test
    for a real bug: xmlgen.py used to keyframe the brightness filter's
    `alpha` property while leaving `level` fixed at 1, which produces no
    visible change at all when rendered - `level` is the property that
    actually scales pixel values (see xmlgen.py's _add_video_fade_filters,
    and MLT's own filter_brightness.yml docs: "alpha ... No alpha channel
    adjustment occurs if this is not set or it equals 1")."""
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
        - {{clip: photo, start: 0, duration: 2, fade_in: 1.5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def brightness(t: float) -> float:
        r, g, b = _mean_rgb(rendered, timestamp=t)
        return (r + g + b) / 3

    dark = brightness(0.0)
    mid = brightness(0.6)
    bright = brightness(1.4)

    assert dark < mid < bright, (
        f"expected brightness to ramp up across the fade_in, got "
        f"dark={dark:.1f} mid={mid:.1f} bright={bright:.1f}"
    )
