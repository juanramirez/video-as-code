"""youtube.py and `video-as-code publish`, against a stand-in for the
YouTube API (youtube._http replaced): nothing here touches YouTube."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

from video_as_code import cli, i18n, youtube
from video_as_code.errors import ValidationErrors
from video_as_code.models import YouTubeSpec
from video_as_code.schema import load_yaml

TOKEN_URI = "https://oauth2.example/token"


class FakeYouTube:
    """Answers youtube._http as the API would, and keeps what was asked of it."""

    def __init__(self, channel: str = "UCme", privacy_after: str | None = None):
        self.channel = channel
        self.privacy_after = privacy_after  # what videos.list says after the upload (None: as asked)
        self.calls: list[tuple[str, str]] = []
        self.uploads: list[dict] = []
        self.chunks: list[tuple[str, int]] = []
        self.updates: list[dict] = []
        self.playlist_items: set[tuple[str, str]] = set()
        self.tokens: list[dict] = []
        self.privacy: dict[str, str] = {}

    def __call__(self, method, url, headers=None, body=None):
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        path = parsed.path.rsplit("/", 1)[-1]
        self.calls.append((method, path))
        if url == TOKEN_URI:
            fields = dict(urllib.parse.parse_qsl(body.decode()))
            self.tokens.append(fields)
            return 200, {}, json.dumps({"access_token": f"tok{len(self.tokens)}", "expires_in": 3600,
                                        "refresh_token": "refresh"}).encode()
        if path == "channels":
            return 200, {}, json.dumps({"items": [{"id": self.channel, "snippet": {"title": "My channel"}}]}).encode()
        if method == "POST" and parsed.netloc == "www.googleapis.com" and "upload" in parsed.path:
            self.uploads.append(json.loads(body))
            return 200, {"Location": f"https://upload.example/session{len(self.uploads)}"}, b""
        if parsed.netloc == "upload.example":
            first, rest = headers["Content-Range"].split(" ")[1].split("-")
            last, total = rest.split("/")
            self.chunks.append((parsed.path, len(body)))
            if int(last) + 1 < int(total):
                return 308, {"Range": f"bytes=0-{last}"}, b""
            video_id = f"vid{len(self.uploads)}"
            self.privacy[video_id] = self.privacy_after or self.uploads[-1]["status"]["privacyStatus"]
            return 200, {}, json.dumps({"id": video_id}).encode()
        if path == "videos" and method == "PUT":
            self.updates.append(json.loads(body))
            return 200, {}, b"{}"
        if path == "videos" and method == "GET":
            return 200, {}, json.dumps({"items": [{"status": {"privacyStatus": self.privacy.get(query["id"], "private")}}]}).encode()
        if path == "playlistItems" and method == "GET":
            found = (query["playlistId"], query["videoId"]) in self.playlist_items
            return 200, {}, json.dumps({"items": [{"id": "x"}] if found else []}).encode()
        if path == "playlistItems" and method == "POST":
            snippet = json.loads(body)["snippet"]
            self.playlist_items.add((snippet["playlistId"], snippet["resourceId"]["videoId"]))
            return 200, {}, b"{}"
        return 404, {}, json.dumps({"error": {"message": f"no route {method} {url}"}}).encode()


@pytest.fixture
def yt(tmp_path: Path, monkeypatch) -> FakeYouTube:
    """The stand-in, a client file, and a saved sign-in that's still good."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    fake = FakeYouTube()
    monkeypatch.setattr(youtube, "_http", fake)
    monkeypatch.setattr(youtube, "CHUNK", 4)  # several pieces for a small file
    youtube.config_dir().mkdir(parents=True)
    youtube.client_file().write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec",
                                                               "token_uri": TOKEN_URI}}))
    youtube._save_token({"access_token": "saved", "refresh_token": "refresh", "expires_at": time.time() + 3600})
    return fake


def _video(tmp_path: Path, body: bytes = b"0123456789") -> Path:
    path = tmp_path / "out.mp4"
    path.write_bytes(body)
    return path


SPEC = YouTubeSpec(title="White Christmas 2017/2018", description="Feliz Navidad", privacy="unlisted",
                   playlists=("PLwc",), tags=("navidad",))


def _publish(tmp_path: Path, spec: YouTubeSpec = SPEC, said: list | None = None, **kwargs):
    said = [] if said is None else said
    return youtube.publish(spec, _video(tmp_path, kwargs.pop("body", b"0123456789")), tmp_path, said.append,
                           yes=kwargs.pop("yes", True), **kwargs)


def test_a_dry_run_says_what_it_would_do_without_signing_in(tmp_path: Path, yt: FakeYouTube):
    said = []
    assert _publish(tmp_path, said=said, dry_run=True) is None
    assert yt.calls == []
    assert any("White Christmas 2017/2018" in line and "unlisted" in line for line in said)
    assert any("PLwc" in line for line in said)


def test_the_first_publish_uploads_in_pieces_and_records_it(tmp_path: Path, yt: FakeYouTube):
    assert _publish(tmp_path) == "vid1"
    resource = yt.uploads[0]
    assert resource["snippet"] == {"title": "White Christmas 2017/2018", "description": "Feliz Navidad",
                                   "categoryId": "22", "tags": ["navidad"]}
    assert resource["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    assert [n for _, n in yt.chunks] == [4, 4, 2]  # 10 bytes in pieces of 4
    assert yt.playlist_items == {("PLwc", "vid1")}
    lock = youtube.read_lock(tmp_path)
    assert lock["video_id"] == "vid1" and lock["channel"] == "UCme" and lock["size"] == 10
    assert (tmp_path / youtube.LOCK_NAME).read_text().startswith("# Written by")


def test_again_with_the_same_file_only_updates_it(tmp_path: Path, yt: FakeYouTube):
    _publish(tmp_path)
    yt.calls.clear()
    retitled = YouTubeSpec(**{**SPEC.__dict__, "title": "New title"})
    assert _publish(tmp_path, retitled) == "vid1"
    assert len(yt.uploads) == 1  # no second upload
    assert yt.updates[0]["id"] == "vid1" and yt.updates[0]["snippet"]["title"] == "New title"
    assert ("POST", "playlistItems") not in yt.calls  # already in it


def test_a_changed_file_needs_replace(tmp_path: Path, yt: FakeYouTube):
    _publish(tmp_path)
    with pytest.raises(youtube.YouTubeError, match="--replace"):
        _publish(tmp_path, body=b"another render")
    said = []
    assert _publish(tmp_path, said=said, body=b"another render", replace=True) == "vid2"
    assert youtube.read_lock(tmp_path)["video_id"] == "vid2"
    assert any("youtu.be/vid1" in line for line in said)  # the old one is still there: told


def test_the_wrong_channel_publishes_nothing(tmp_path: Path, yt: FakeYouTube):
    spec = YouTubeSpec(**{**SPEC.__dict__, "channel": "UCsomeone_else"})
    with pytest.raises(youtube.YouTubeError, match="UCsomeone_else"):
        _publish(tmp_path, spec)
    assert not yt.uploads and not (tmp_path / youtube.LOCK_NAME).exists()


def test_it_asks_before_uploading_and_a_no_uploads_nothing(tmp_path: Path, yt: FakeYouTube):
    asked = []
    result = youtube.publish(SPEC, _video(tmp_path), tmp_path, lambda text: None,
                             ask=lambda q: asked.append(q) or "n")
    assert result is None and len(asked) == 1 and "My channel" in asked[0]
    assert not yt.uploads and not (tmp_path / youtube.LOCK_NAME).exists()


def test_a_video_youtube_kept_private_comes_with_how_to_change_it(tmp_path: Path, yt: FakeYouTube):
    yt.privacy_after = "private"  # an API project Google hasn't audited
    said = []
    _publish(tmp_path, said=said)
    guide = next(line for line in said if "studio.youtube.com/video/vid1/edit" in line)
    assert "unlisted" in guide and "2." in guide


def test_no_client_file_says_how_to_make_one(tmp_path: Path, yt: FakeYouTube):
    youtube.client_file().unlink()
    youtube.token_file().unlink()
    with pytest.raises(youtube.YouTubeError) as exc:
        _publish(tmp_path)
    assert "console.cloud.google.com" in str(exc.value) and "YouTube Data API v3" in str(exc.value)


def test_an_expired_sign_in_is_refreshed(tmp_path: Path, yt: FakeYouTube):
    youtube._save_token({"access_token": "old", "refresh_token": "refresh", "expires_at": time.time() - 10})
    assert youtube.access_token(lambda text: None) == "tok1"
    assert yt.tokens[0]["grant_type"] == "refresh_token"
    assert json.loads(youtube.token_file().read_text())["access_token"] == "tok1"
    assert youtube.token_file().stat().st_mode & 0o077 == 0  # only the user can read it


def test_signing_in_through_the_browser(tmp_path: Path, yt: FakeYouTube):
    youtube.token_file().unlink()

    def browser(url: str) -> None:
        # what Google does once the user agrees: back to the local server, with a code
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert query["code_challenge_method"] == "S256" and query["scope"] == youtube.SCOPES
        back = query["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "the-code", "state": query["state"]})
        urllib.request.urlopen(back, timeout=10).read()

    assert youtube.access_token(lambda text: None, open_browser=browser) == "tok1"
    exchange = yt.tokens[0]
    assert exchange["grant_type"] == "authorization_code" and exchange["code"] == "the-code"
    assert exchange["code_verifier"]  # PKCE


def _project(tmp_path: Path, publish: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(f"project: {{id: p, title: Trip, fps: 25, width: 320, height: 240{publish}}}\n"
                    "bin: []\ntimeline: {tracks: []}\n", encoding="utf-8")
    return path


def test_the_block_is_read_from_the_yaml(tmp_path: Path):
    spec = load_yaml(_project(tmp_path, ", publish: {youtube: {privacy: public, playlists: PLx, category: 19}}")).project.youtube
    assert spec == YouTubeSpec(title="Trip", privacy="public", playlists=("PLx",), category="19")


@pytest.mark.parametrize("block, wrong", [
    ("{youtube: {privacy: hidden}}", "privacy"),
    ("{youtube: {made_for_kids: maybe}}", "made_for_kids"),
    ("{youtube: {tags: [1, 2]}}", "tags"),
    ("{youtube: {colour: red}}", "colour"),
    ("{vimeo: {}}", "project.publish"),
])
def test_a_wrong_block_is_rejected(tmp_path: Path, block: str, wrong: str):
    with pytest.raises(ValidationErrors) as exc:
        load_yaml(_project(tmp_path, f", publish: {block}"))
    assert wrong in str(exc.value)


def test_cli_publish_dry_run(tmp_path: Path, yt: FakeYouTube, capsys):
    project = _project(tmp_path, ", publish: {youtube: {privacy: unlisted}}")
    assert cli.main(["publish", str(project), str(_video(tmp_path)), "--dry-run"]) == 0
    assert "Trip" in capsys.readouterr().err and yt.calls == []


def test_cli_publish_without_a_block(tmp_path: Path, capsys):
    project = _project(tmp_path, "")
    assert cli.main(["publish", str(project), str(_video(tmp_path))]) == 1
    assert "project.publish.youtube" in capsys.readouterr().err


@pytest.mark.parametrize("lang, privacy, shown", [("es", "unlisted", "oculto"), ("es", "private", "privado"),
                                                  ("es", "public", "público"), ("en", "unlisted", "unlisted")])
def test_privacy_is_said_as_youtube_says_it_in_that_language(tmp_path: Path, yt: FakeYouTube, lang, privacy, shown):
    original = i18n.LOCALE
    i18n.set_locale(lang)
    try:
        said = []
        _publish(tmp_path, YouTubeSpec(title="T", privacy=privacy), said=said, dry_run=True)
    finally:
        i18n.set_locale(original)
    assert said[0].endswith(shown)


def test_with_no_terminal_to_ask_in_nothing_is_uploaded(tmp_path: Path, yt: FakeYouTube):
    def no_terminal(question: str) -> str:
        raise EOFError

    said = []
    assert youtube.publish(SPEC, _video(tmp_path), tmp_path, said.append, ask=no_terminal) is None
    assert not yt.uploads and "--yes" in said[-1]
