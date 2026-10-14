"""splitrender.py: planning, trimming and cache keys on small synthetic
XML, and `render --split` end to end with the real melt."""
from __future__ import annotations

import json
import subprocess
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

    assert cli.main(["render", str(yaml_path), "-o", str(output), "--split", "--max-mem", "1"]) == 0
    err = capsys.readouterr().err
    assert err.count("unchanged") == 9  # 8 segments + the sound, all from the cache
    assert _frames(output) == 75
