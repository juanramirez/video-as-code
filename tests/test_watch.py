"""watch.py: a save re-renders only the segments it touches and plays
from there - with the real melt - and the loop around it."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_as_code import cli, probe, splitrender, watch


def _project(tmp_path: Path, assets_dir: Path, last_colour_clip: str = "photo") -> Path:
    # (stills of at most default_image_duration each: a longer one is cut short)
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: w, title: W, fps: 25, width: 320, height: 240}}
bin:
  - {{id: photo, path: {assets_dir / "photo.png"}}}
  - {{id: photo2, path: {assets_dir / "photo2.png"}}}
  - {{id: clip, path: {assets_dir / "clip.mp4"}}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 4}}
        - {{clip: photo2, start: 4, duration: 2}}
        - {{clip: {last_colour_clip}, start: 6, duration: 2}}
""",
        encoding="utf-8",
    )
    return yaml_path


@pytest.fixture
def with_melt(melt, monkeypatch, tmp_path):
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VIDEO_AS_CODE_NO_SCOPE", "1")
    monkeypatch.setattr(splitrender, "PROGRESS_FILE", tmp_path / "render-progress.json")
    monkeypatch.setattr(watch, "WATCH_SEGMENT_S", 1.0)  # (20-frame segments: 10 of them in 8 s)


def _frames(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip())


def test_segments_no_longer_than_the_watch_length():
    assert watch.segment_frames(25) == 160  # 6.4 s: the longest MIN_FRAMES * 2^k within 8 s
    assert watch.segment_frames(60) == 320
    assert watch.segment_frames(1) == splitrender.MIN_FRAMES


def test_a_save_renders_only_what_it_touched(tmp_path, assets_dir, with_melt):
    yaml_path = _project(tmp_path, assets_dir)
    first = watch.render_once(yaml_path, None, None, lambda text: None)
    assert first.changed == (0, 199)  # all of it, the first time
    assert first.start_s() == 0
    assert _frames(first.output) == 200
    assert first.output.parent == tmp_path / splitrender.CACHE_DIR / "watch" / "project"

    _project(tmp_path, assets_dir, last_colour_clip="clip")  # the clip at 6 s replaced
    second = watch.render_once(yaml_path, None, None, lambda text: None)
    # the segments around 6 s were rendered again (and the margin trim()
    # keeps), but the picture changes where the clip does: from frame 150
    assert second.changed == (150, 199)
    assert second.start_s() == pytest.approx(150 / 25 - watch.LEAD_IN_S)
    assert _frames(second.output) == 200

    third = watch.render_once(yaml_path, None, None, lambda text: None)
    assert third.changed is None and third.start_s() is None  # nothing new


class _Player(watch.Player):
    def __init__(self):
        super().__init__()
        self.shown = []

    def show(self, file, start, report):
        self.shown.append((file, start))


def test_the_loop_renders_on_each_save_and_plays_from_there(tmp_path, assets_dir, with_melt):
    yaml_path = _project(tmp_path, assets_dir)
    said = []
    saves = iter([lambda: None, lambda: _project(tmp_path, assets_dir, last_colour_clip="clip")])

    def sleep(_):  # (each pause of the loop: the next save, if any)
        next(saves, lambda: None)()

    player = _Player()
    watch.run(yaml_path, proxy=None, report=said.append, renders=2, sleep=sleep, player=player, poll_s=0)
    assert [start for _, start in player.shown][0] == 0
    assert player.shown[1][1] == pytest.approx(5.0)
    assert any("changed from" in s for s in said)


def test_a_save_that_doesnt_validate_keeps_the_last(tmp_path, assets_dir, with_melt, monkeypatch):
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text("project: {id: w}\n", encoding="utf-8")
    said, player = [], _Player()
    watch.run(yaml_path, proxy=None, report=said.append, renders=1, sleep=lambda _: None, player=player, poll_s=0)
    assert not player.shown
    assert any("title" in s for s in said) and any("last preview stays" in s for s in said)


def test_the_player_command(monkeypatch, tmp_path):
    monkeypatch.delenv(watch.PLAYER_ENV, raising=False)
    monkeypatch.setattr(watch.shutil, "which", lambda name: name if name == "vlc" else None)
    assert watch.player_command(Path("/p.mp4"), 12.345) == ["vlc", "--start-time=12.35", "/p.mp4"]
    monkeypatch.setattr(watch.shutil, "which", lambda name: None)
    assert watch.player_command(Path("/p.mp4"), 1) is None
    monkeypatch.setenv(watch.PLAYER_ENV, "celluloid --start {start} '{file}'")
    assert watch.player_command(Path("/a b.mp4"), 1) == ["celluloid", "--start", "1.00", "/a b.mp4"]


def test_watched_files_skip_hidden_folders(tmp_path):
    (tmp_path / "project.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "a.yml").write_text("x", encoding="utf-8")
    (tmp_path / ".derived").mkdir()
    (tmp_path / ".derived" / "b.yaml").write_text("x", encoding="utf-8")
    assert sorted(p.name for p in watch.watched(tmp_path / "project.yaml")) == ["a.yml", "project.yaml"]


def test_clock():
    assert watch.clock(83.44) == "1:23.4"
    assert watch.clock(5) == "0:05.0"


def test_the_command_without_a_file(tmp_path, capsys):
    assert cli.main(["watch", str(tmp_path / "nope.yaml")]) == 1
    assert "nope.yaml" in capsys.readouterr().err
