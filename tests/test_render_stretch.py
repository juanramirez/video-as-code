"""`render --from/--to`: just a stretch of the video, frame for frame
what the whole render has there - in one melt run or with --split."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from video_as_code import cli, probe, splitrender
from video_as_code.timecode import clock_name, parse_clock


def test_times():
    assert [parse_clock(t) for t in ("80", "80.5", "1:20", "1:20.5", "1:02:03")] == [80, 80.5, 80, 80.5, 3723]
    for bad in ("", "1:", "a", "1:x", "1:60", "-1", "1:2:3:4", "1:60:00"):
        with pytest.raises(ValueError):
            parse_clock(bad)
    assert [clock_name(s) for s in (45, 80, 80.5)] == ["45s", "1m20s", "1m20.5s"]


@pytest.fixture
def project(tmp_path, assets_dir, melt, monkeypatch) -> Path:
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


def _frames(path: Path) -> int:
    return int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                               "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout)


def _psnr(whole: Path, first: int, last: int, stretch: Path) -> float:
    """The stretch against the whole render's frames first..last, in dB
    (both encoded lossily, each from its own start: alike, not equal)."""
    run = subprocess.run(
        ["ffmpeg", "-i", str(whole), "-i", str(stretch), "-filter_complex",
         f"[0:v]trim=start_frame={first}:end_frame={last + 1},setpts=PTS-STARTPTS[a];[a][1:v]psnr", "-f", "null", "-"],
        capture_output=True, text=True)
    average = re.search(r"PSNR .*average:(\S+)", run.stderr).group(1)
    return float("inf") if average == "inf" else float(average)


def _has_sound(path: Path) -> bool:
    return "audio" in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                                      str(path)], capture_output=True, text=True).stdout


@pytest.mark.parametrize("split", [[], ["--split", "--max-mem", "1"]])
def test_a_stretch_is_those_frames_of_the_whole(project, split, capsys):
    whole = project.with_name("whole.mp4")
    assert cli.main(["render", str(project), "-o", str(whole), *split]) == 0
    assert cli.main(["render", str(project), "--from", "0.6", "--to", "0:02.2", *split]) == 0
    stretch = project.with_name("project_0.6s-2.2s.mp4")  # (named so as not to replace the whole one)
    assert "40 frames" in capsys.readouterr().out
    assert _frames(stretch) == 40
    assert _psnr(whole, 15, 54, stretch) > 40  # (a frame off it, the same comparison gives ~24)
    assert _psnr(whole, 16, 55, stretch) < 30
    assert _has_sound(stretch)


def test_a_stretch_with_split_leaves_the_whole_renders_cache(project):
    assert cli.main(["render", str(project), "--split", "--max-mem", "1", "-o", str(project.with_name("w.mp4"))]) == 0
    cache = project.parent / splitrender.CACHE_DIR
    before = {p.name for p in cache.glob("split_*")}
    assert cli.main(["render", str(project), "--split", "--max-mem", "1", "--from", "1", "--to", "2"]) == 0
    assert before <= {p.name for p in cache.glob("split_*")}


@pytest.mark.parametrize("times, said", [(["--from", "2", "--to", "9"], "lasts 0:03.0"), (["--from", "2", "--to", "1"], "lasts"),
                                          (["--from", "1:x"], "Not a time: '1:x'")])
def test_times_outside_or_wrong(project, times, said, capsys):
    assert cli.main(["render", str(project), *times]) == 1
    assert said in capsys.readouterr().err
