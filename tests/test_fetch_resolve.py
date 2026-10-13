"""fetch_resolve.py: the YAML text edits (which must keep comments and
layout) and the dialogue, driven by scripted answers."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from video_as_code import cli, fetch, fetch_resolve

from test_fetch import FakeAlbum, FakeLinks, _write_project

HEAD = "project: {id: p1, title: T, fps: 25, width: 320, height: 240}\n"
TAIL = "timeline:\n  tracks: []\n"


def _bin(text: str) -> dict:
    return {str(e["id"]): e for e in yaml.safe_load(text)["bin"]}


# --- text edits ---------------------------------------------------------------


def test_replace_source_everywhere_it_is_used_keeping_comments():
    text = HEAD + (
        "bin:\n"
        "  # the slide\n"
        "  - id: a\n"
        "    source: OLD.mp4   # keep me\n"
        "  - {id: b, source: 'OLD.mp4'}\n"
        "  - id: c\n"
        "    derive: {source: OLD.mp4, trim: [0, 1]}\n"
        "  - id: d\n"
        "    source: OLD.mp4.bak\n"
    ) + TAIL
    new = fetch_resolve.replace_source(text, "OLD.mp4", "NEW.mp4")
    assert "    source: NEW.mp4   # keep me\n" in new
    assert "  # the slide\n" in new
    clips = _bin(new)
    assert clips["b"]["source"] == "NEW.mp4"
    assert clips["c"]["derive"]["source"] == "NEW.mp4"
    assert clips["d"]["source"] == "OLD.mp4.bak"  # not a prefix match


def test_source_to_url_only_for_the_given_clips():
    text = HEAD + "bin:\n  - id: a\n    source: X.mp4  # why\n  - {id: b, source: X.mp4}\n  - {id: c, source: X.mp4}\n" + TAIL
    new = fetch_resolve.source_to_url(text, ["a", "b"], "X.mp4", "https://example.com/x.mp4")
    clips = _bin(new)
    assert clips["a"] == {"id": "a", "url": "https://example.com/x.mp4"}
    assert clips["b"] == {"id": "b", "url": "https://example.com/x.mp4"}
    assert clips["c"]["source"] == "X.mp4"
    assert "  # why\n" in new


@pytest.mark.parametrize(
    "entry",
    [
        "  - id: m\n    url: https://a/1  # first\n",
        "  - id: m\n    url:\n      - https://a/1\n",
        "  - id: m\n    url: [https://a/1]\n",
        "  - {id: m, url: 'https://a/1'}\n",
        "  - {id: m, url: ['https://a/1']}\n",
    ],
)
def test_add_link_puts_it_first_whatever_the_layout(entry: str):
    text = HEAD + "bin:\n" + entry + "  - id: other\n    path: x.png\n" + TAIL
    new = fetch_resolve.add_link(text, "m", "https://b/2")
    assert _bin(new)["m"]["url"] == ["https://b/2", "https://a/1"]
    assert _bin(new)["other"]["path"] == "x.png"


def test_add_link_keeps_a_trailing_comment():
    text = HEAD + "bin:\n  - id: m\n    url: https://a/1  # first\n" + TAIL
    assert "# first" in fetch_resolve.add_link(text, "m", "https://b/2")


# --- the dialogue -------------------------------------------------------------


def _answers(*values: str):
    it = iter(values)

    def ask(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return ask


def _problem_for(project: Path, monkeypatch) -> fetch.Problem:
    with pytest.raises(fetch.FetchError) as exc:
        fetch.fetch(project)
    [problem] = exc.value.problems
    return problem


def test_rename_writes_the_yaml_and_the_next_fetch_finds_it(tmp_path: Path, monkeypatch):
    FakeAlbum({"real.jpg": b"R"}).install(monkeypatch)
    project = _write_project(tmp_path, "  - id: c0\n    source: wrong.jpg  # note")
    problem = _problem_for(project, monkeypatch)
    outcome = fetch_resolve.resolve(project, [problem], ask=_answers("a", "real.jpg"), say=lambda s: None)
    assert outcome.changed
    assert "source: real.jpg  # note" in project.read_text()
    assert fetch.fetch(project).downloaded == ["real.jpg"]


def test_a_link_replaces_the_album_item(tmp_path: Path, monkeypatch):
    FakeAlbum({}).install(monkeypatch)
    FakeLinks({"https://d/x": (b"X", ".jpg")}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: c0, source: gone.jpg}")
    problem = _problem_for(project, monkeypatch)
    fetch_resolve.resolve(project, [problem], ask=_answers("u", "https://d/x"), say=lambda s: None)
    assert fetch.fetch(project).downloaded == ["c0"]


def test_a_library_photos_link_is_refused(tmp_path: Path, monkeypatch):
    FakeAlbum({}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: c0, source: gone.jpg}")
    problem = _problem_for(project, monkeypatch)
    said: list[str] = []
    before = project.read_text()
    outcome = fetch_resolve.resolve(
        project, [problem], ask=_answers("u", "https://photos.google.com/photo/AF1Qip", "s"), say=said.append
    )
    assert project.read_text() == before
    assert outcome.skipped == {"gone.jpg"} and not outcome.changed
    assert any("album" in s for s in said)


def test_a_derive_input_cant_become_a_link(tmp_path: Path, monkeypatch):
    FakeAlbum({}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: cut, derive: {source: gone.mp4, trim: [0, 1]}}")
    problem = _problem_for(project, monkeypatch)
    said: list[str] = []
    fetch_resolve.resolve(project, [problem], ask=_answers("s"), say=said.append)
    assert not any(s.strip().startswith("u)") for s in said)


def test_a_local_file_is_used_here_and_kept_by_later_fetches(tmp_path: Path, monkeypatch):
    album = FakeAlbum({})
    album.install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: c0, source: gone.jpg}")
    problem = _problem_for(project, monkeypatch)
    mine = tmp_path / "mine.jpg"
    mine.write_bytes(b"MINE")
    fetch_resolve.resolve(project, [problem], ask=_answers("l", f"'{mine}'"), say=lambda s: None)
    assert fetch.fetch(project).up_to_date == ["gone.jpg"]
    assert (tmp_path / "assets" / "gone.jpg").read_bytes() == b"MINE"


def test_a_local_file_for_a_url_clip(tmp_path: Path, monkeypatch):
    FakeLinks({}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: music, url: 'https://youtu.be/x'}")
    problem = _problem_for(project, monkeypatch)
    song = tmp_path / "song.MP3"
    song.write_bytes(b"S")
    fetch_resolve.resolve(project, [problem], ask=_answers("l", str(song)), say=lambda s: None)
    assert fetch.fetch(project).up_to_date == ["music"]
    assert (tmp_path / "assets" / "music.mp3").read_bytes() == b"S"


# --- the CLI loop -------------------------------------------------------------


def test_cli_asks_then_fetches_again(tmp_path: Path, monkeypatch):
    FakeAlbum({"real.jpg": b"R"}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: c0, source: wrong.jpg}")
    monkeypatch.setattr("builtins.input", _answers("a", "real.jpg"))
    assert cli._run_fetch(project, interactive=True)
    assert (tmp_path / "assets" / "19700101_c0.jpg").read_bytes() == b"R"


def test_cli_gives_up_when_everything_left_was_skipped(tmp_path: Path, monkeypatch):
    FakeAlbum({}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: c0, source: gone.jpg}")
    monkeypatch.setattr("builtins.input", _answers("s"))
    assert not cli._run_fetch(project, interactive=True)


def test_cli_doesnt_ask_when_not_interactive(tmp_path: Path, monkeypatch):
    FakeAlbum({}).install(monkeypatch)
    project = _write_project(tmp_path, "  - {id: c0, source: gone.jpg}")

    def no_input(prompt):
        raise AssertionError("asked")

    monkeypatch.setattr("builtins.input", no_input)
    assert not cli._run_fetch(project, interactive=False)
