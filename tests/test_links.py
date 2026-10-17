"""links.py: telling links apart, and the YouTube path through a fake
`yt-dlp` (VIDEO_AS_CODE_YT_DLP) so the real subprocess handling runs."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from video_as_code import gphotos, links


@pytest.mark.parametrize(
    "url, kind",
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://music.youtube.com/watch?v=abc", "youtube"),
        ("https://drive.google.com/file/d/FILE_id-1/view?usp=sharing", "drive"),
        ("https://example.com/song.mp3", "http"),
    ],
)
def test_kind(url: str, kind: str):
    assert links.kind(url) == kind


@pytest.mark.parametrize(
    "url",
    [
        "https://drive.google.com/file/d/FILE_id-1/view?usp=sharing",
        "https://drive.google.com/open?id=FILE_id-1",
        "https://drive.google.com/uc?export=download&id=FILE_id-1",
    ],
)
def test_drive_file_id(url: str):
    assert links.drive_file_id(url) == "FILE_id-1"


def test_drive_link_without_an_id_fails(tmp_path: Path):
    with pytest.raises(links.LinkError):
        links.download("https://drive.google.com/drive/my-drive", tmp_path, "music")


def test_download_broken_off_halfway_is_a_link_error(tmp_path: Path, monkeypatch):
    class Broken(io.BytesIO):
        headers = {"Content-Type": "audio/mpeg"}

        def geturl(self):
            return "https://example.com/song.mp3"

        def read(self, *args):
            raise TimeoutError("The read operation timed out")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(gphotos, "_open", lambda url: Broken())
    with pytest.raises(links.LinkError, match="broke off"):
        links.download("https://example.com/song.mp3", tmp_path, "music")
    assert list(tmp_path.iterdir()) == []


def _fake_yt_dlp(tmp_path: Path, monkeypatch, body: str) -> Path:
    """A stand-in for yt-dlp that gets the same arguments; `body` runs
    with `out` (the -o template) and `fmt` (the -f value) defined."""
    script = tmp_path / "fake_yt_dlp.py"
    script.write_text(
        "import sys\nargs = sys.argv[1:]\nout = args[args.index('-o') + 1]\nfmt = args[args.index('-f') + 1]\n" + body,
        encoding="utf-8",
    )
    monkeypatch.setenv("VIDEO_AS_CODE_YT_DLP", f"{sys.executable} {script}")
    return script


def test_youtube_asks_for_the_m4a_audio_and_keeps_its_extension(tmp_path: Path, monkeypatch):
    _fake_yt_dlp(
        tmp_path,
        monkeypatch,
        "path = out.replace('%(ext)s', 'm4a')\nopen(path, 'w').write(fmt)\nprint(path)\n",
    )
    dest = tmp_path / "assets"
    got = links.download("https://youtu.be/abc", dest, "music")
    assert got == dest / "music.m4a"
    assert got.read_text().startswith("bestaudio[ext=m4a]")
    assert sorted(p.name for p in dest.iterdir()) == ["music.m4a"]  # no .part left behind


def test_youtube_failure_is_a_link_error_with_yt_dlps_reason(tmp_path: Path, monkeypatch):
    _fake_yt_dlp(tmp_path, monkeypatch, "sys.stderr.write('ERROR: HTTP Error 403: Forbidden\\n')\nsys.exit(1)\n")
    with pytest.raises(links.LinkError, match="403"):
        links.download("https://youtu.be/abc", tmp_path / "assets", "music")


def test_missing_yt_dlp_is_a_link_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEO_AS_CODE_YT_DLP", str(tmp_path / "no-such-yt-dlp"))
    with pytest.raises(links.LinkError, match="VIDEO_AS_CODE_YT_DLP"):
        links.download("https://youtu.be/abc", tmp_path / "assets", "music")


def test_youtube_video_asks_for_the_whole_video_as_an_mp4(tmp_path: Path, monkeypatch):
    # project.sources.reference: video + audio, merged into an mp4
    _fake_yt_dlp(
        tmp_path,
        monkeypatch,
        "path = out.replace('%(ext)s', 'mp4')\nopen(path, 'w').write(fmt + ' ' + ' '.join(sys.argv))\nprint(path)\n",
    )
    got = links.download("https://youtu.be/abc", tmp_path / "reference", "old", video=True)
    assert got == tmp_path / "reference" / "old.mp4"
    text = got.read_text()
    assert text.startswith("bestvideo[ext=mp4]+bestaudio[ext=m4a]") and "--merge-output-format mp4" in text
