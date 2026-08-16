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

from kdenlive_as_code import probe, xmlgen
from kdenlive_as_code.models import ResolvedProject
from kdenlive_as_code.schema import load_and_resolve


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


def test_melt_hides_a_hidden_track(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `hidden:` on a track (new this cycle): renders
    black instead of the track's actual content - a real surprise found
    while implementing it (not assumed from kdenlive's source alone):
    `hide` on the track's own tractor turns out to genuinely affect
    melt's rendered output, not just kdenlive's own editor UI (a locked/
    hidden toggle could plausibly have been editor-only metadata with no
    render-time effect - it isn't, at least for `hidden`)."""
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
      hidden: true
      clips:
        - {{clip: photo, start: 0, duration: 1}}
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

    r, g, b = _mean_rgb(rendered, timestamp=0.5)
    assert r < 10 and g < 10 and b < 10, f"expected a hidden track to render black, got {(r, g, b)}"


def test_melt_mutes_an_audio_muted_track(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `audio_muted:` on a track (new this cycle,
    v0.5): the whole track should render silent, same as `hidden:`
    genuinely blacks out a video track's render - not just an editor-UI
    toggle. Distinct mechanism from the per-clip `mute:` filter already
    supported (this is `hide`'s bit 2 on the track's own tractor, not a
    `volume`/`gain=0` filter on the entry)."""
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
      audio_muted: true
      clips:
        - {{clip: tone, start: 0, out: 2}}
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
    assert muted < -90, f"expected near-silence with audio_muted: true, got {muted}dB"


def test_melt_loads_a_project_with_notes(tmp_path: Path, assets_dir: Path, melt):
    """`notes:` on the project (new this cycle) has no visual/audible
    effect - melt just needs to accept the real
    `kdenlive:documentnotes` property without choking on it."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240, notes: "Needs a color pass."}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    xml_out = tmp_path / "roundtrip.xml"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"xml:{xml_out}", "all_frames=0"])

    _assert_no_fatal_load_error(result)
    assert xml_out.exists() and xml_out.stat().st_size > 0


def test_melt_loads_a_project_with_a_bin_folder(tmp_path: Path, assets_dir: Path, melt):
    """`folder:` on a bin clip (new this cycle) has no visual/audible
    effect (it's a Project Bin organization concept only) - melt just
    needs to accept the real `kdenlive:folder.-1.<id>` property and a
    non-default `kdenlive:folderid` on the clip's producer without
    choking on either."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
    folder: "B-Roll"
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    xml_out = tmp_path / "roundtrip.xml"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"xml:{xml_out}", "all_frames=0"])

    _assert_no_fatal_load_error(result)
    assert xml_out.exists() and xml_out.stat().st_size > 0


def test_melt_loads_a_project_with_guides(tmp_path: Path, assets_dir: Path, melt):
    """`guides:` (new this cycle) has no visual/audible effect - melt just
    needs to accept the real JSON `kdenlive:sequenceproperties.guides`
    property (`{"pos", "comment", "type"}` entries) without choking on it,
    unlike the hardcoded `"[]"` placeholder this replaces."""
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
        - {{clip: photo, start: 0, duration: 2}}
guides:
  - {{at: 0.5, text: "First"}}
  - {{at: 1.5, text: "Second", category: 3}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    xml_out = tmp_path / "roundtrip.xml"
    result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"xml:{xml_out}", "all_frames=0"])

    _assert_no_fatal_load_error(result)
    assert xml_out.exists() and xml_out.stat().st_size > 0


def test_melt_can_render_generated_project(tmp_path: Path, assets_dir: Path, melt):
    """Stronger than the structural check above: actually decodes and
    re-encodes every frame instead of just dumping the parsed structure, and
    confirms the rendered output's duration matches what kdenlive-as-code computed —
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
        f"{expected_duration}s kdenlive-as-code computed ({resolved.total_frames} frames @ {resolved.fps.fps}fps)"
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


def _mean_volume_db_channel(path: Path, channel: int, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner",
            "-i", str(path),
            "-ss", str(start), "-t", str(duration),
            "-af", f"pan=mono|c0=c{channel},volumedetect",
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


def test_melt_applies_a_real_audio_pan(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `pan:` on an audio track (new this cycle,
    makes the already-emitted `panner` filter a real control instead of
    the hardcoded disable=1 placeholder): full left (-1.0) should render
    loud on the left channel and near-silent on the right - not just that
    the filter's `start`/`disable` properties got written plausibly."""
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
      pan: -1.0
      clips:
        - {{clip: tone, start: 0, out: 2}}
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

    left = _mean_volume_db_channel(rendered, channel=0, start=0.5, duration=1.0)
    right = _mean_volume_db_channel(rendered, channel=1, start=0.5, duration=1.0)

    assert left > right + 20, (
        f"expected full-left pan to be much louder on the left channel than "
        f"the right, got left={left}dB right={right}dB"
    )
    assert right < -40, f"expected near-silence on the right channel at full-left pan, got {right}dB"


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


def _color_stddev(path: Path, timestamp: float) -> float:
    """Standard deviation of pixel values across a whole frame - near 0
    for a plain solid color, much higher once block-shifting/color
    glitching mixes in patches of other colors. Used to detect `glitch`
    (frei0r.glitch0r) without needing to know exactly where the glitched
    blocks land."""
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
    # Isolate a single channel (R): a solid-color frame has ~zero spatial
    # variation *within* one channel, even though the raw interleaved
    # RGB byte stream itself oscillates a lot between channels - mixing
    # channels together would make a plain solid color look "high
    # variance" for no real reason.
    r = result.stdout[0::3]
    mean = sum(r) / len(r)
    variance = sum((b - mean) ** 2 for b in r) / len(r)
    return variance**0.5


def _rgb_stddev(path: Path, timestamp: float) -> float:
    """Same idea as `_color_stddev`, but sums the per-channel variance
    across R, G, *and* B instead of reading just one - `_color_stddev`
    alone isn't a safe default for every color combination: a gradient
    wash whose color choice happens to share one channel's value with
    the base color (e.g. an orange #ff8800 wash on a pure-red base -
    both ~255 in R) would look like ~zero variance on R alone even
    though the wash is clearly, strongly visible in G. Summing all three
    channels avoids having to know in advance which channel a given
    color pair actually varies in."""
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
    total = 0.0
    for offset in (0, 1, 2):
        channel = result.stdout[offset::3]
        mean = sum(channel) / len(channel)
        total += sum((b - mean) ** 2 for b in channel) / len(channel)
    return total**0.5


def _mean_rgb_at_x(path: Path, timestamp: float, frac_x: float, width: int = 320) -> tuple[float, float, float]:
    """Like `_mean_rgb`, but only a thin vertical strip at `frac_x` (0..1)
    of the frame width - lets a test tell a spatially-varying wipe apart
    from a uniform cross-fade, which `_mean_rgb`'s whole-frame average
    can't."""
    x = max(0, min(width - 4, int(width * frac_x)))
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-ss", str(timestamp), "-i", str(path),
            "-frames:v", "1",
            "-vf", f"crop=4:in_h:{x}:0",
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


def test_scenes_dissolve_produces_a_real_crossfade(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `transition: dissolve` (scenes.py): renders a
    two-clip scene (a red still, then a blue one) and confirms the
    transition window is a genuine cross-fade - a smooth, monotonic blend
    from red to blue - not a hard cut or a fade through black. This is the
    "never verified against a real kdenlive project" case scenes.py's own
    docstring flags; needs `qtblend` (see conftest.py's `melt_with_qtblend`)
    since dissolve fundamentally depends on cross-track alpha compositing."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: dissolve
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction(t: float) -> float:
        r, _g, b = _mean_rgb(rendered, timestamp=t)
        return r / (r + b + 1e-6)

    # photo.png is red, photo2.png is blue (assets_dir, conftest.py); the
    # transition spans [1.5, 2.5)s.
    before = red_fraction(1.3)
    early = red_fraction(1.7)
    mid = red_fraction(2.0)
    late = red_fraction(2.3)
    after = red_fraction(2.7)

    assert before > 0.95, f"expected pure red before the transition, got red_fraction={before:.2f}"
    assert after < 0.05, f"expected pure blue after the transition, got red_fraction={after:.2f}"
    assert before > early > mid > late > after, (
        f"expected a monotonic red->blue blend across the transition, got "
        f"{before=:.2f} {early=:.2f} {mid=:.2f} {late=:.2f} {after=:.2f}"
    )


def test_scenes_wipe_produces_a_real_spatial_wipe(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `transition: wipe` (scenes.py, new this cycle):
    renders a two-clip scene (red -> blue, `luma: linear_x`) and confirms
    the reveal genuinely sweeps left-to-right - at a given moment, the
    left edge of the frame can already be blue while the right edge is
    still red - not a uniform cross-fade (which `_mean_rgb`'s whole-frame
    average couldn't tell apart from a real wipe; `_mean_rgb_at_x` samples
    a specific column instead). Regression test for a real bug found while
    building this: an MLT tractor applies its transitions in declaration
    order, and once a qtblend compositing transition had already run
    against a track, a later `luma` transition between two *other* tracks
    stopped progressing over time entirely (every sampled frame landed on
    the same side of the wipe, `in`/`out` seemingly ignored) - fixed by
    emitting the wipe transitions before the qtblend ones in xmlgen.py."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: wipe
    transition_duration: 1.0
    luma: linear_x
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction_at(t: float, frac_x: float) -> float:
        r, _g, b = _mean_rgb_at_x(rendered, timestamp=t, frac_x=frac_x)
        return r / (r + b + 1e-6)

    # photo.png is red, photo2.png is blue (assets_dir, conftest.py); the
    # transition spans [1.5, 2.5)s. linear_x is dark on the left, bright
    # on the right, and the left side of a luma map reveals first.
    assert red_fraction_at(1.3, 0.1) > 0.95, "expected pure red before the transition"

    left_early = red_fraction_at(1.7, 0.1)
    right_early = red_fraction_at(1.7, 0.9)
    assert left_early < 0.5 < right_early, (
        f"expected the left edge to have already turned blue while the right "
        f"edge is still red, got {left_early=:.2f} {right_early=:.2f}"
    )

    assert red_fraction_at(2.7, 0.9) < 0.05, "expected pure blue after the transition"


def _band_volume_db(path: Path, freq: int, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner",
            "-i", str(path),
            "-ss", str(start), "-t", str(duration),
            "-af", f"bandpass=f={freq}:width_type=h:w=100,volumedetect",
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


def test_scenes_audio_dissolve_produces_a_real_crossfade(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for an audio-only `scenes:` scene (new this cycle,
    fixes `scenes:` transitions being hardcoded to a video-typed track):
    a 220Hz tone dissolving into an 880Hz tone should produce a genuine
    crossfade - both bands present during the overlap, 220Hz fading out
    while 880Hz fades in - not a hard cut. Doesn't need `qtblend` (unlike
    the video dissolve test): an audio crossfade is just two volume-faded
    clips on two audio tracks, summed by the a_track=0 `mix` transition
    every audio track already gets."""
    low = tmp_path / "low.wav"
    high = tmp_path / "high.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(low)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=3", str(high)],
        check=True,
    )

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: dissolve
    transition_duration: 1.0
    clips:
      - {low}
      - {high}
    duration: 4.0
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

    # the transition spans [1.5, 2.5)s (two 2s clips, 1s overlap).
    low_before = _band_volume_db(rendered, 220, start=1.3, duration=0.1)
    low_mid = _band_volume_db(rendered, 220, start=2.0, duration=0.1)
    low_after = _band_volume_db(rendered, 220, start=2.7, duration=0.1)
    high_before = _band_volume_db(rendered, 880, start=1.3, duration=0.1)
    high_mid = _band_volume_db(rendered, 880, start=2.0, duration=0.1)
    high_after = _band_volume_db(rendered, 880, start=2.7, duration=0.1)

    assert low_before > low_mid > low_after, (
        f"expected the 220Hz band to fade out across the transition, got "
        f"before={low_before}dB mid={low_mid}dB after={low_after}dB"
    )
    assert high_after > high_mid > high_before, (
        f"expected the 880Hz band to fade in across the transition, got "
        f"before={high_before}dB mid={high_mid}dB after={high_after}dB"
    )


def test_scenes_affine_zoom_produces_a_real_zoom(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `transition: affine`, `style: zoom` (new this
    cycle, a third transition engine alongside dissolve's alpha fade and
    wipe's luma map): renders a two-clip scene (red -> blue) and confirms
    the incoming clip genuinely grows from a small centered rect - during
    the transition, the center should already be blue while the edge is
    still red, unlike a uniform dissolve (`_mean_rgb`'s whole-frame
    average couldn't tell these apart) or a spatial wipe (which sweeps
    left-to-right, not center-outward)."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: affine
    style: zoom
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction(x: int, y: int, t: float) -> float:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(rendered), "-frames:v", "1",
             "-vf", f"crop=4:4:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30, check=True,
        )
        data = result.stdout
        n = len(data) // 3
        r = sum(data[0::3]) / n
        b = sum(data[2::3]) / n
        return r / (r + b + 1e-6)

    # photo.png is red, photo2.png is blue; the transition spans [1.5, 2.5)s.
    assert red_fraction(160, 120, 1.3) > 0.95, "expected pure red before the transition"
    center_early = red_fraction(160, 120, 1.7)
    edge_early = red_fraction(10, 10, 1.7)
    assert center_early < 0.5 < edge_early, (
        f"expected the center to already be blue (zoomed-in reveal) while the "
        f"edge is still red, got {center_early=:.2f} {edge_early=:.2f}"
    )
    assert red_fraction(10, 10, 2.7) < 0.05, "expected pure blue everywhere after the transition"


def test_scenes_affine_slide_produces_a_real_slide(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `transition: affine`, `style: slide`: the
    incoming clip should enter from the right edge and sweep left, the
    same spatial-sweep technique `wipe`'s own test uses (unlike `zoom`,
    which reveals from the center outward)."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: affine
    style: slide
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction_at(t: float, frac_x: float) -> float:
        r, _g, b = _mean_rgb_at_x(rendered, timestamp=t, frac_x=frac_x)
        return r / (r + b + 1e-6)

    # photo.png is red, photo2.png is blue; the transition spans [1.5, 2.5)s.
    assert red_fraction_at(1.3, 0.9) > 0.95, "expected pure red before the transition"
    left_early = red_fraction_at(1.7, 0.1)
    right_early = red_fraction_at(1.7, 0.9)
    assert right_early < 0.5 < left_early, (
        f"expected the incoming clip to enter from the right while the left "
        f"edge is still the outgoing clip, got {left_early=:.2f} {right_early=:.2f}"
    )
    assert red_fraction_at(2.7, 0.1) < 0.05, "expected pure blue everywhere after the transition"


def test_scenes_iris_produces_a_real_iris_reveal(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `transition: iris` (new this cycle, a single-clip
    `frei0r.alphaspot` filter composited via qtblend, not a genuine
    two-source MLT transition like wipe/affine): renders a two-clip scene
    (red -> blue) and confirms the incoming clip is revealed through a
    growing shape centered on the frame - center already blue while the
    edge is still red, the same spatial signature `zoom` (affine) has,
    distinguishing it from wipe's left-to-right sweep."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: iris
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction(x: int, y: int, t: float) -> float:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(rendered), "-frames:v", "1",
             "-vf", f"crop=4:4:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30, check=True,
        )
        data = result.stdout
        n = len(data) // 3
        r = sum(data[0::3]) / n
        b = sum(data[2::3]) / n
        return r / (r + b + 1e-6)

    # photo.png is red, photo2.png is blue; the transition spans [1.5, 2.5)s.
    assert red_fraction(160, 120, 1.3) > 0.95, "expected pure red before the transition"
    center_mid = red_fraction(160, 120, 2.0)
    edge_mid = red_fraction(5, 5, 2.0)
    assert center_mid < 0.5 < edge_mid, (
        f"expected the center to already be blue (iris reveal) while the "
        f"corner is still red, got {center_mid=:.2f} {edge_mid=:.2f}"
    )
    assert red_fraction(5, 5, 2.7) < 0.05, "expected pure blue everywhere after the transition"


def test_scenes_flip_produces_a_real_spatial_sweep(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `transition: flip` (new this cycle, a
    single-clip `frei0r.3dflippo` filter, real perspective-correct 3D
    rotation confirmed against a checkerboard test source during backlog
    research - solid colors alone can't show perspective foreshortening,
    only that *something* eventually changes): the reveal should sweep
    left-to-right as the rotation opens up, the same spatial technique
    `wipe`'s own test uses."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: flip
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def red_fraction_at(t: float, frac_x: float) -> float:
        r, _g, b = _mean_rgb_at_x(rendered, timestamp=t, frac_x=frac_x)
        return r / (r + b + 1e-6)

    # photo.png is red, photo2.png is blue; the transition spans [1.5, 2.5)s.
    assert red_fraction_at(1.3, 0.9) > 0.95, "expected pure red before the transition"
    left_mid = red_fraction_at(2.0, 0.05)
    right_mid = red_fraction_at(2.0, 0.9)
    assert left_mid < 0.5 < right_mid, (
        f"expected the reveal to have already reached the left edge while "
        f"the right edge is still red, got {left_mid=:.2f} {right_mid=:.2f}"
    )
    assert red_fraction_at(2.7, 0.9) < 0.05, "expected pure blue everywhere after the transition"


def test_scenes_glitch_produces_real_block_shifting(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `transition: glitch` (new this cycle,
    frei0r.glitch0r - a single-clip filter burst dressing a plain cut,
    not a two-source reveal like iris/flip: glitch0r writes no alpha, so
    it doesn't need `qtblend`/a second track at all, unlike every other
    transition this project has). A solid-color clip should render with
    much higher pixel variance during the glitch burst window (block-
    shifted patches of other colors mixed in) than outside it."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: glitch
    transition_duration: 0.4
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
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

    # 2 clips, duration 4.0, no real overlap -> each clip spans 2.0s;
    # the glitch burst dresses [1.6, 2.0)s (outgoing) and [2.0, 2.4)s
    # (incoming).
    calm = _color_stddev(rendered, timestamp=1.0)
    glitched = _color_stddev(rendered, timestamp=1.85)
    assert glitched > calm + 5, (
        f"expected clearly higher pixel variance during the glitch burst, "
        f"got calm={calm:.2f} glitched={glitched:.2f}"
    )


def test_scenes_cairogradient_produces_a_visible_color_wash(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `transition: cairogradient` (v0.6, another
    single-clip filter burst dressing a plain cut, same BURST_TRANSITIONS
    shape `glitch` uses - frei0r.cairogradient writes no alpha either,
    confirmed by render before wiring this in: a green clip on top of a
    blue background never lets any blue through). A solid-color photo
    should render with much higher pixel variance during the burst window
    (the diagonal orange gradient blended across an otherwise flat
    color) than outside it - same detection technique
    `test_scenes_glitch_produces_real_block_shifting` uses."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: cairogradient
    transition_duration: 0.4
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
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

    calm = _rgb_stddev(rendered, timestamp=1.0)
    washed = _rgb_stddev(rendered, timestamp=1.85)
    assert washed > calm + 5, (
        f"expected clearly higher pixel variance during the cairogradient burst, "
        f"got calm={calm:.2f} washed={washed:.2f}"
    )


def test_scenes_delaygrab_produces_visible_temporal_ghosting(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `transition: delaygrab` (v0.6, another
    single-clip filter burst dressing a plain cut, same BURST_TRANSITIONS
    shape `glitch` uses - frei0r.delaygrab writes no alpha either, and
    exposes no adjustable parameters at all, confirmed via `melt -query
    filter=frei0r.delaygrab` and kdenlive's own effect definition for it).

    Unlike glitch/cairogradient, delaygrab's effect is motion-dependent
    (it blits stale/delayed content from a fixed sample region, so a
    static frame shows nothing) and localized rather than frame-wide, so
    the whole-frame stddev technique the other two bursts use doesn't
    apply here. Instead this compares the same scene at the same instant
    with `transition: delaygrab` vs `transition: none` (a plain cut, no
    burst dressing at all) - a real burst should render a visibly
    different frame at that exact timestamp than a plain cut does,
    confirmed by real render (not assumed from the standalone probe used
    to first verify the mechanism, matching the ROADMAP's requirement
    that the exact shape be confirmed against the generator's own
    output)."""
    def render(transition: str) -> Path:
        yaml_path = tmp_path / f"project_{transition}.yaml"
        yaml_path.write_text(
            f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: {transition}
    transition_duration: 0.4
    clips:
      - {assets_dir / "clip.mp4"}
      - {assets_dir / "clip.mp4"}
    duration: 4.0
""",
            encoding="utf-8",
        )
        resolved = load_and_resolve(yaml_path)
        kdenlive_path = tmp_path / f"project_{transition}.kdenlive"
        xmlgen.write(resolved, kdenlive_path)
        rendered = tmp_path / f"rendered_{transition}.mp4"
        result = _run_melt(melt, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
        _assert_no_fatal_load_error(result)
        assert rendered.exists() and rendered.stat().st_size > 0
        return rendered

    delaygrab_rendered = render("delaygrab")
    baseline_rendered = render("none")

    def frame_bytes(path: Path, timestamp: float) -> bytes:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30, check=True,
        )
        return result.stdout

    # 2 clips, duration 4.0, no real overlap -> each clip spans 2.0s; the
    # burst dresses [1.6, 2.0)s (outgoing) - same window the glitch test
    # samples from.
    a = frame_bytes(delaygrab_rendered, 1.85)
    b = frame_bytes(baseline_rendered, 1.85)
    n = min(len(a), len(b))
    mean_abs_diff = sum(abs(x - y) for x, y in zip(a, b)) / n if n else 0.0
    assert mean_abs_diff > 1.0, (
        f"expected delaygrab's burst to render visibly differently from a "
        f"plain cut at the same instant, got mean_abs_diff={mean_abs_diff:.3f}"
    )


def test_chroma_key_reveals_the_track_below(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `chroma_key:` (new this cycle, v0.5's headline
    feature): a green-screen clip (green background + a red subject
    square) on top of a blue background clip should render with the
    green keyed out to blue while the red subject stays red - not
    `frei0r.alphaspot`/`3dflippo`'s "same fade shape" story, and not
    `avfilter.chromakey` either (tried first: a real "Cannot set src
    colorspace" warning and no visible keying effect at all on this
    project's real melt build). `frei0r.bluescreen0r` - kdenlive's own
    mechanism for this - is what's actually used."""
    greenscreen = tmp_path / "greenscreen.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x00ff00:size=320x240",
            "-vf", "drawbox=x=110:y=70:w=100:h=100:color=red:t=fill",
            "-frames:v", "1", str(greenscreen),
        ],
        check=True,
    )
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: bg
    path: {assets_dir / "photo2.png"}
  - id: fg
    path: {greenscreen}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: bg, start: 0, duration: 1}}
    - type: video
      clips:
        - {{clip: fg, start: 0, duration: 1, chroma_key: "#00ff00"}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def mean_rgb_in_box(x: int, y: int) -> tuple[float, float, float]:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "0.1", "-i", str(rendered), "-frames:v", "1",
             "-vf", f"crop=10:10:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30, check=True,
        )
        data = result.stdout
        n = len(data) // 3
        return (sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n)

    # photo2.png (assets_dir) is blue; the green corner (well outside the
    # red subject square, which spans x:110..210 y:70..170) should key
    # out to it, while the subject itself (sampled at its center) stays
    # red.
    corner_r, corner_g, corner_b = mean_rgb_in_box(10, 10)
    assert corner_b > corner_g + 20 and corner_b > corner_r + 20, (
        f"expected the green background to key out to blue, got {(corner_r, corner_g, corner_b)}"
    )

    subject_r, subject_g, subject_b = mean_rgb_in_box(155, 115)
    assert subject_r > subject_g + 20 and subject_r > subject_b + 20, (
        f"expected the red subject to stay red, got {(subject_r, subject_g, subject_b)}"
    )


def test_melt_renders_a_real_text_overlay(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `overlay_text:` (new this cycle, MLT's own
    `dynamictext` filter): a real melt render of a clip with an overlay
    actually shows the text, not just that the filter got attached
    without erroring - same rigor `type: title` bin clips got in
    `test_melt_renders_a_real_title_clip`. A pure-black frame (mean
    brightness ~0) would mean the text never rendered."""
    black = tmp_path / "black.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:size=320x240", "-frames:v", "1", str(black)],
        check=True,
    )
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: black
    path: {black}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: black, start: 0, duration: 2, overlay_text: "#timecode#"}}
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

    r, g, b = _mean_rgb(rendered, timestamp=1.0)
    brightness = (r + g + b) / 3
    assert brightness > 5, f"expected visible white text on a black background, got near-black frame (brightness={brightness:.2f})"


def test_ken_burns_produces_a_real_pan_zoom(tmp_path: Path, assets_dir: Path, melt_with_qtblend):
    """Empirical check for `ken_burns_from:`/`ken_burns_to:` (v0.6's
    headline feature): a single-clip `affine` filter with a keyframed
    `transition.rect` (the "transition." prefix is how a bare `affine`
    *filter* forwards properties to the transition it wraps internally -
    an unprefixed `rect`, like scenes.py's `zoom`/`slide` use directly on
    the affine *transition*, produces no animation at all when set on
    the filter instead - confirmed empirically before wiring this in).
    A centered blue square on a red background, zoomed from 100% to 150%
    centered (`[0,0,100,100]` -> `[-25,-25,150,150]`), should still show
    red just outside the square's original edge near the start, but blue
    there by the end, as the square visibly grows - matches kdenlive's
    own "Position and Zoom" effect mechanism exactly
    (`~/dev/kdenlive/data/effects/pan_zoom.xml`, same `transition.rect`
    parameter, not modeled by analogy)."""
    square = tmp_path / "square.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:size=320x240",
            "-vf", "drawbox=x=140:y=100:w=40:h=40:color=blue:t=fill",
            "-frames:v", "1", str(square),
        ],
        check=True,
    )
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: sq
    path: {square}
timeline:
  tracks:
    - type: video
      clips:
        - clip: sq
          start: 0
          duration: 2
          ken_burns_from: [0, 0, 100, 100]
          ken_burns_to: [-25, -25, 150, 150]
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    rendered = tmp_path / "rendered.mp4"
    result = _run_melt(melt_with_qtblend, [str(kdenlive_path), "-consumer", f"avformat:{rendered}"])
    _assert_no_fatal_load_error(result)
    assert rendered.exists() and rendered.stat().st_size > 0

    def blue_fraction(x: int, y: int, t: float) -> float:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(rendered), "-frames:v", "1",
             "-vf", f"crop=4:4:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30, check=True,
        )
        data = result.stdout
        n = len(data) // 3
        r = sum(data[0::3]) / n
        b = sum(data[2::3]) / n
        return b / (r + b + 1e-6)

    # The square spans x:140..180 at 100%, growing to x:130..190 at 150%
    # (centered zoom) - x=185 is just outside the square at the start but
    # inside it by the end.
    assert blue_fraction(185, 120, 0.04) < 0.05, "expected pure red just outside the square before the zoom"
    assert blue_fraction(185, 120, 1.9) > 0.9, "expected the square to have visibly grown to cover this point by the end"


def test_focus_rect_produces_a_real_blur_outside_the_rect(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `focus_rect:`/`focus_blur:` (v0.6, MLT's own
    `pillar_echo` filter - kdenlive-native, Meltytech, not frei0r): keeps
    the rect sharp, blurs everything outside it. A fine checkerboard test
    pattern (high spatial-frequency detail everywhere) should render with
    a much higher pixel-value stddev inside the rect than just outside
    it - verified against the generator's own output, not just the
    standalone probe used to first confirm the mechanism (same rigor
    `dance`'s ROADMAP entry documents failing under, and why that one was
    dropped instead of shipped)."""
    checker = tmp_path / "checker.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:size=320x240",
            "-vf", "geq=lum='if(mod(floor(X/8)+floor(Y/8)\\,2)\\,255\\,0)':cb=128:cr=128",
            "-frames:v", "1", str(checker),
        ],
        check=True,
    )
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: checker
    path: {checker}
timeline:
  tracks:
    - type: video
      clips:
        - clip: checker
          start: 0
          duration: 1
          focus_rect: [35, 35, 30, 30]
          focus_blur: 20
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

    def local_stddev(y0: int, y1: int, x0: int, x1: int, t: float) -> float:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(rendered), "-frames:v", "1",
             "-vf", f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=30, check=True,
        )
        data = result.stdout
        n = len(data)
        mean = sum(data) / n
        variance = sum((b - mean) ** 2 for b in data) / n
        return variance**0.5

    # focus_rect covers x:35-65%, y:35-65% (i.e. roughly x:112-208, y:84-156
    # of a 320x240 frame) - (120,140) sits inside it, (10,10) is well outside.
    inside = local_stddev(120, 150, 140, 170, 0.5)
    outside = local_stddev(10, 40, 10, 40, 0.5)
    assert inside > outside + 50, (
        f"expected the checkerboard detail to stay sharp inside focus_rect and blur outside it, "
        f"got inside={inside:.1f} outside={outside:.1f}"
    )


def test_melt_respects_video_speed_change(tmp_path: Path, assets_dir: Path, melt):
    """Empirical check for `speed:` (timewarp) on a video clip: at 2x, the
    3s testsrc clip (assets_dir) should occupy 1.5s on the timeline - not
    just a structural check that a `timewarp` producer got written, but
    that melt actually renders that duration. `duration:`/`out:` are left
    unset, so this also verifies schema.py resolves in/out/duration
    against the *warped* length correctly (`resolved.total_frames`)."""
    if not probe.ffprobe_available():
        pytest.skip("ffprobe not available: can't inspect the rendered output")

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, speed: 2.0}}
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
        f"{expected_duration}s kdenlive-as-code computed at 2x speed "
        f"({resolved.total_frames} frames @ {resolved.fps.fps}fps)"
    )
    assert expected_duration < 2.0, f"sanity check: 2x of a 3s clip should be ~1.5s, got {expected_duration}s"


def test_melt_respects_audio_speed_change(tmp_path: Path, assets_dir: Path, melt):
    """Same empirical check as test_melt_respects_video_speed_change, but
    for an audio-only clip at 0.5x (slower, not faster) - `speed:` isn't
    video-specific, per the timewarp MLT producer itself and the real
    kdenlive project this was modeled on (see xmlgen.py's
    _build_media_element)."""
    if not probe.ffprobe_available():
        pytest.skip("ffprobe not available: can't inspect the rendered output")

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
        - {{clip: tone, start: 0, speed: 0.5}}
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

    probed = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
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
        f"{expected_duration}s kdenlive-as-code computed at 0.5x speed "
        f"({resolved.total_frames} frames @ {resolved.fps.fps}fps)"
    )
    assert expected_duration > 9.0, f"sanity check: 0.5x of a 5s tone should be ~10s, got {expected_duration}s"


def test_melt_respects_reverse_speed_change(tmp_path: Path, melt):
    """Empirical check for negative `speed:` (reverse timewarp, new this
    cycle): a clip that's red for its first second and blue for its
    second should render blue-then-red at `speed: -1.0` (reverse, same
    rate) - not just that a `timewarp` producer with a negative
    `warp_speed` got written, but that melt actually plays it backwards."""
    bicolor = tmp_path / "bicolor.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=25:duration=1",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=25:duration=1",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", str(bicolor),
        ],
        check=True,
    )

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {bicolor}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, speed: -1.0}}
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

    early_r, _early_g, early_b = _mean_rgb(rendered, timestamp=0.1)
    late_r, _late_g, late_b = _mean_rgb(rendered, timestamp=1.7)

    assert early_b > early_r + 20, f"expected the reversed clip to start blue, got r={early_r} b={early_b}"
    assert late_r > late_b + 20, f"expected the reversed clip to end red, got r={late_r} b={late_b}"


def test_melt_renders_a_real_title_clip(tmp_path: Path, melt):
    """Empirical check for `type: title` bin clips (titles.py, new this
    cycle): a real melt render of a title actually shows the text, not
    just that a `kdenlivetitle` producer got written without errors. A
    pure-black frame (mean brightness ~0) would mean the text never
    rendered - white text against titles.py's default transparent-black
    background should measurably raise the average."""
    if not probe.ffprobe_available():
        pytest.skip("ffprobe not available: can't inspect the rendered output")

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        """
project: {id: p1, title: T, fps: 25, width: 320, height: 240}
bin:
  - id: title1
    type: title
    text: "Hello World"
timeline:
  tracks:
    - type: video
      clips:
        - {clip: title1, start: 0, duration: 2}
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

    r, g, b = _mean_rgb(rendered, timestamp=1.0)
    brightness = (r + g + b) / 3
    assert brightness > 5, f"expected visible white text on a black background, got near-black frame (brightness={brightness:.2f})"
