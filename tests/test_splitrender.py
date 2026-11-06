"""splitrender.py: planning, trimming and cache keys on small synthetic
XML, and `render --split` end to end with the real melt."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from video_as_code import cli, probe, splitrender
from video_as_code.splitrender import MAX_FRAMES, MIN_FRAMES, Segment

# --- plan ---------------------------------------------------------------------


def test_plan_takes_whole_blocks_when_everything_fits():
    segments = splitrender.plan(3000, [(0, 2999, "a")], {"a": 10}, base_mb=100, budget_mb=1000)
    assert [(s.first, s.last) for s in segments] == [(0, MAX_FRAMES - 1), (MAX_FRAMES, 2 * MAX_FRAMES - 1), (2 * MAX_FRAMES, 2999)]


def test_plan_halves_only_where_clips_pile_up():
    # a heavy clip at frames 100-109: only the blocks containing it get small
    entries = [(0, 1279, "light"), (100, 109, "heavy")]
    segments = splitrender.plan(1280, entries, {"light": 10, "heavy": 500}, base_mb=100, budget_mb=300)
    assert all(s.est_mb <= 300 for s in segments if s.last < 100 or s.first > 109)
    assert segments[-1] == Segment(640, 1279, 110)  # the half without it, whole
    small = [s for s in segments if s.first <= 109 and s.last >= 100]
    assert all(s.last - s.first + 1 == MIN_FRAMES for s in small)
    # contiguous, aligned to their own size
    assert [s.first for s in segments] == sorted(s.first for s in segments)
    assert all(a.last + 1 == b.first for a, b in zip(segments, segments[1:]))
    assert all(s.first % (s.last - s.first + 1) == 0 for s in segments[:-1])


def test_plan_is_local_an_edit_only_changes_the_segments_around_it():
    entries = [(f, f + 30, f"c{f}") for f in range(0, 2560, 40)]
    costs = {pid: 60 for _, _, pid in entries}
    before = splitrender.plan(2560, entries, costs, base_mb=100, budget_mb=500)
    costs["c2000"] = 2000  # a much heavier clip near the end
    after = splitrender.plan(2560, entries, costs, base_mb=100, budget_mb=500)
    assert [s for s in before if s.last < MAX_FRAMES] == [s for s in after if s.last < MAX_FRAMES]


def test_plan_renders_an_over_budget_minimum_segment_anyway():
    segments = splitrender.plan(20, [(0, 19, "huge")], {"huge": 5000}, base_mb=100, budget_mb=300)
    assert [(s.first, s.last) for s in segments] == [(0, 9), (10, 19)]
    assert all(s.est_mb > 300 for s in segments)


# --- trim / keys ----------------------------------------------------------------

_XML = """<mlt root="{root}">
  <profile width="320" height="240"/>
  <producer id="producer_black"><property name="mlt_service">color</property></producer>
  <chain id="early"><property name="mlt_service">avformat</property><property name="resource">a.mp4</property></chain>
  <chain id="late"><property name="mlt_service">avformat</property><property name="resource">b.mp4</property></chain>
  <chain id="song"><property name="mlt_service">avformat</property><property name="resource">a.mp4</property></chain>
  <playlist id="main_bin"><entry producer="early"/><entry producer="late"/><entry producer="song"/></playlist>
  <playlist id="pic"><entry producer="early" in="0" out="99"/><blank length="400"/><entry producer="late" in="0" out="99"/></playlist>
  <playlist id="snd"><entry producer="song" in="0" out="599"/></playlist>
  <tractor id="t"><track producer="pic" hide="audio"/><track producer="snd" hide="video"/></tractor>
</mlt>"""


def _root(tmp_path: Path) -> ET.Element:
    (tmp_path / "a.mp4").write_bytes(b"A")
    (tmp_path / "b.mp4").write_bytes(b"B")
    return ET.fromstring(_XML.format(root=tmp_path))


def _ids(root: ET.Element) -> set[str]:
    return {e.get("id") for e in root if e.tag in ("producer", "chain")}


def test_trim_blanks_far_entries_and_drops_their_producers(tmp_path: Path):
    trimmed = splitrender.trim(_root(tmp_path), 0, 9, margin=20)
    pic = trimmed.find("playlist[@id='pic']")
    assert [c.tag for c in pic] == ["entry", "blank", "blank"]  # same length, nothing moved
    assert pic[2].get("length") == "100"
    assert _ids(trimmed) == {"producer_black", "early", "song"}
    assert [e.get("producer") for e in trimmed.find("playlist[@id='main_bin']")] == ["early", "song"]


def test_trim_audio_only_keeps_just_what_sounds(tmp_path: Path):
    trimmed = splitrender.trim(_root(tmp_path), 0, 599, audio_only=True)
    assert _ids(trimmed) == {"producer_black", "song"}


def test_segment_key_ignores_id_numbering_but_not_files(tmp_path: Path):
    root = _root(tmp_path)
    key = splitrender.segment_key(root, tmp_path, ["0-9"])
    renamed = ET.fromstring(ET.tostring(root).replace(b'"early"', b'"chain7"'))
    assert splitrender.segment_key(renamed, tmp_path, ["0-9"]) == key
    assert splitrender.segment_key(root, tmp_path, ["0-19"]) != key
    (tmp_path / "a.mp4").write_bytes(b"A, replaced")
    assert splitrender.segment_key(root, tmp_path, ["0-9"]) != key


def test_audio_key_ignores_picture_edits(tmp_path: Path):
    root = _root(tmp_path)
    key = splitrender.segment_key(splitrender.trim(root, 0, 599, audio_only=True), tmp_path, [], audio=True)
    pic = root.find("playlist[@id='pic']")
    pic.remove(pic[2])  # a picture clip removed: no sample changes
    assert splitrender.segment_key(splitrender.trim(root, 0, 599, audio_only=True), tmp_path, [], audio=True) == key


# --- end to end -----------------------------------------------------------------


def _frames(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip())


def _streams(path: Path) -> set[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return {s["codec_type"] for s in json.loads(out)["streams"]}


def test_render_split_end_to_end_and_cached(tmp_path: Path, assets_dir: Path, melt, monkeypatch, capsys):
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VIDEO_AS_CODE_NO_SCOPE", "1")
    progress_file = tmp_path / "render-progress.json"
    monkeypatch.setattr(splitrender, "PROGRESS_FILE", progress_file)
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - {{id: photo, path: {assets_dir / "photo.png"}}}
  - {{id: clip, path: {assets_dir / "clip.mp4"}}}
  - {{id: tone, path: {assets_dir / "tone.mp3"}}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
        - {{clip: clip, start: 1, source_in: 0, source_out: 2}}
    - type: audio
      clips:
        - {{clip: tone, start: 0, source_in: 0, source_out: 3}}
""",
        encoding="utf-8",
    )
    output = tmp_path / "out.mp4"
    # a 1 MB budget: every segment ends up at the minimum size
    assert cli.main(["render", str(yaml_path), "-o", str(output), "--split", "--max-mem", "1"]) == 0
    assert _frames(output) == 75  # 3 s at 25 fps, frame-exact across 8 segments
    assert _streams(output) == {"video", "audio"}
    err = capsys.readouterr().err
    assert "8 segment(s)" in err
    state = json.loads(progress_file.read_text(encoding="utf-8"))
    assert (state["phase"], state["done"], state["total"], state["segments"]) == ("done", 75, 75, 8)
    assert state["output"] == str(output.resolve())

    assert cli.main(["render", str(yaml_path), "-o", str(output), "--split", "--max-mem", "1"]) == 0
    err = capsys.readouterr().err
    assert err.count("unchanged") == 9  # 8 segments + the sound, all from the cache
    assert _frames(output) == 75


@pytest.fixture
def split_project(tmp_path: Path, assets_dir: Path, melt, monkeypatch) -> Path:
    """The end-to-end test's project (3 s, 8 segments at --max-mem 1), with the real melt."""
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VIDEO_AS_CODE_NO_SCOPE", "1")
    monkeypatch.setattr(splitrender, "PROGRESS_FILE", tmp_path / "render-progress.json")
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - {{id: photo, path: {assets_dir / "photo.png"}}}
  - {{id: clip, path: {assets_dir / "clip.mp4"}}}
  - {{id: tone, path: {assets_dir / "tone.mp3"}}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
        - {{clip: clip, start: 1, source_in: 0, source_out: 2}}
    - type: audio
      clips:
        - {{clip: tone, start: 0, source_in: 0, source_out: 3}}
""",
        encoding="utf-8",
    )
    return yaml_path


def _render_split(yaml_path: Path) -> int:
    return cli.main(["render", str(yaml_path), "-o", str(yaml_path.parent / "out.mp4"), "--split", "--max-mem", "1"])


def _short_once(monkeypatch, name: str, short_value, times: int = 1) -> list[Path]:
    """splitrender.<name> (video_frames / audio_seconds) reports `short_value`
    for the first `times` files measured, then the truth; returns those files."""
    real, seen = getattr(splitrender, name), []

    def measure(path: Path):
        if len(seen) < times:
            seen.append(path)
            return short_value
        return real(path)

    monkeypatch.setattr(splitrender, name, measure)
    return seen


def test_short_only_below_the_frames_wanted():
    assert splitrender._short(9, 10) and not splitrender._short(10, 10) and not splitrender._short(11, 10)
    assert not splitrender._short(None, 10)  # (unmeasurable: not taken as short)


def test_a_segment_that_comes_out_short_is_rendered_again(split_project: Path, monkeypatch, capsys):
    _short_once(monkeypatch, "video_frames", 3)
    assert _render_split(split_project) == 0
    assert _frames(split_project.parent / "out.mp4") == 75
    assert "came out short (3 of" in capsys.readouterr().err


def test_a_segment_short_twice_stops_the_render(split_project: Path, monkeypatch, capsys):
    _short_once(monkeypatch, "video_frames", 3, times=2)
    assert _render_split(split_project) != 0
    assert "short twice" in capsys.readouterr().err
    assert not list((split_project.parent / ".render_cache").rglob("split_000000_*.mkv"))  # nothing short cached


def test_a_segment_cached_short_is_rendered_again(split_project: Path, monkeypatch, capsys):
    assert _render_split(split_project) == 0
    capsys.readouterr()
    _short_once(monkeypatch, "video_frames", 3)  # the first cached segment, as if it had been cached short
    assert _render_split(split_project) == 0
    assert capsys.readouterr().err.count("unchanged") == 8  # 7 segments + the sound; the short one rendered again
    assert _frames(split_project.parent / "out.mp4") == 75


def test_a_sound_that_comes_out_short_is_rendered_again(split_project: Path, monkeypatch, capsys):
    _short_once(monkeypatch, "audio_seconds", 2.0)
    assert _render_split(split_project) == 0
    assert _frames(split_project.parent / "out.mp4") == 75  # -shortest didn't cut the picture to 2 s
    assert "sound came out short (2.0 s of 3.0 s)" in capsys.readouterr().err


def test_a_sound_short_twice_stops_the_render(split_project: Path, monkeypatch, capsys):
    _short_once(monkeypatch, "audio_seconds", 2.0, times=2)
    assert _render_split(split_project) != 0
    assert "sound came out short twice" in capsys.readouterr().err


def test_melt_progress_reaches_the_callback():
    # melt reports "Current Frame: N, percentage: P" on stderr, \r-separated
    script = (
        "import sys, time\n"
        "for f in (3, 7, 12):\n"
        "    sys.stderr.write(f'Current Frame: {f:>10}, percentage: {f}\\r'); sys.stderr.flush(); time.sleep(0.05)\n"
        "sys.stderr.write('oops at the end\\n')\n"
    )
    seen: list[int] = []
    code, tail = splitrender._run_melt_with_progress([sys.executable, "-c", script], dict(os.environ), seen.append)
    assert code == 0
    assert seen and seen[-1] == 12 and seen == sorted(seen)
    assert tail.strip().endswith("oops at the end")



# --- joining the segments ---------------------------------------------------------


def test_the_concat_list_gives_each_segment_its_exact_length(tmp_path: Path):
    parts = [tmp_path / "a.mkv", tmp_path / "b.mkv"]
    text = splitrender.concat_list(parts, [Segment(0, 9, 1.0), Segment(10, 49, 1.0)], (30, 1))
    assert text.splitlines() == [f"file '{parts[0].resolve()}'", "duration 0.333333333",
                                 f"file '{parts[1].resolve()}'", "duration 1.333333333"]


def test_frame_rate_comes_from_the_profile():
    root = ET.fromstring('<mlt><profile frame_rate_num="30000" frame_rate_den="1001"/></mlt>')
    assert splitrender.frame_rate(root) == (30000, 1001)
    assert splitrender.frame_rate(ET.fromstring("<mlt/>")) == (30, 1)


@pytest.mark.skipif(not probe.ffmpeg_available(), reason="needs ffmpeg")
def test_many_short_segments_join_without_drifting(tmp_path: Path):
    """Matroska keeps milliseconds: 30 segments of 10 frames at 30 fps
    joined by their own durations would end 10 ms early (0.333 s each);
    with the list's exact lengths the last frame is where it belongs."""
    parts, segments = [], []
    for k in range(30):
        part = tmp_path / f"s{k:02d}.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=32x18:rate=30", "-frames:v", "10",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(part)], check=True, capture_output=True)
        parts.append(part)
        segments.append(Segment(k * 10, k * 10 + 9, 1.0))
    listing = tmp_path / "concat.txt"
    listing.write_text(splitrender.concat_list(parts, segments, (30, 1)), encoding="utf-8")
    out = tmp_path / "joined.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:v", "copy", str(out)],
                   check=True, capture_output=True)
    times = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True).stdout.split()
    assert len(times) == 300
    assert abs(float(times[-1]) - 299 / 30) < 0.002  # (without the durations: 9.957 - 10 ms early)
