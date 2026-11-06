"""`video-as-code publish`: the rendered video onto YouTube, as the YAML's
`project.publish.youtube` says (models.YouTubeSpec).

YouTube Data API v3 over plain HTTP (urllib, as the rest of the tool's
downloads): no Google client library. Signing in is OAuth for an
installed app - the user's own "Desktop app" OAuth client, created once
in Google Cloud and saved as CLIENT_FILE (its JSON as Google gives it); the
first run opens the browser, Google sends the answer back to a one-off
local server (127.0.0.1, PKCE), and the tokens are kept in TOKEN_FILE,
readable only by the user. Never in the YAML or the repo.

What was published is recorded next to the YAML in LOCK_NAME (video id,
channel, the file's size and sha256): run again with the same file, only
the title, description, privacy, tags and playlists are updated; with a
different file it stops, and uploads it as a new video only with
`replace` (YouTube can't swap a video's file).

An upload costs ~1600 of a project's 10 000 daily quota units. And an API
project Google hasn't audited gets its uploads locked to private whatever
`privacy` says: when the video comes back private and the YAML wanted
otherwise, the user is told so and walked through changing it by hand.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from .errors import KdenliveAsCodeError
from .i18n import msg
from .models import YouTubeSpec

LOCK_NAME = "publish.lock.yaml"
CLIENT_ENV = "VIDEO_AS_CODE_YOUTUBE_CLIENT"
SCOPES = "https://www.googleapis.com/auth/youtube"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024  # (a multiple of 256 KiB, as resumable uploads want)
STUDIO_URL = "https://studio.youtube.com/video/{id}/edit"
CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"


class YouTubeError(KdenliveAsCodeError):
    pass


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "video-as-code"


def client_file() -> Path:
    return Path(os.environ[CLIENT_ENV]).expanduser() if os.environ.get(CLIENT_ENV) else config_dir() / "youtube_client.json"


def token_file() -> Path:
    return config_dir() / "youtube_token.json"


# --- HTTP ---------------------------------------------------------------------


def _http(method: str, url: str, headers: Optional[dict] = None, body: Optional[bytes] = None) -> tuple[int, dict, bytes]:
    """(status, headers, body) - an HTTP error is a status, not an exception
    (the resumable upload's 308 included). The one place that touches the
    network: tests replace it."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""
    except OSError as exc:
        raise YouTubeError(msg("youtube.network", error=exc)) from exc


def _error_text(body: bytes) -> str:
    try:
        error = json.loads(body)["error"]
        return error.get("message") or str(error)
    except (ValueError, KeyError, TypeError):
        return body.decode("utf-8", "replace")[:300]


# --- signing in ---------------------------------------------------------------


def _client() -> dict:
    path = client_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        client = data.get("installed") or data.get("web") or data
        return {"client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
                "auth_uri": client.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": client.get("token_uri", "https://oauth2.googleapis.com/token")}
    except FileNotFoundError:
        raise YouTubeError(msg("youtube.no_client", path=path, url=CLOUD_CREDENTIALS_URL, env=CLIENT_ENV)) from None
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        raise YouTubeError(msg("youtube.bad_client", path=path, error=exc)) from exc


def _save_token(token: dict) -> None:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        json.dump(token, fh)
    tmp.replace(path)


def _token_request(client: dict, fields: dict) -> dict:
    status, _, body = _http("POST", client["token_uri"], {"Content-Type": "application/x-www-form-urlencoded"},
                            urllib.parse.urlencode({"client_id": client["client_id"],
                                                    "client_secret": client["client_secret"], **fields}).encode())
    if status != 200:
        raise YouTubeError(msg("youtube.sign_in_failed", error=_error_text(body)))
    got = json.loads(body)
    got["expires_at"] = time.time() + float(got.get("expires_in", 3600)) - 60
    return got


def authorize(report: Callable[[str], None], open_browser: Callable[[str], object] = webbrowser.open) -> dict:
    """Signs in through the browser (OAuth for installed apps, PKCE, a
    one-off local server for the answer); saves and returns the token."""
    client = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    answer: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if query.get("state") == state:
                answer.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg("youtube.browser_done").encode("utf-8"))

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = client["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "access_type": "offline",
        "prompt": "consent",
    })
    report(msg("youtube.sign_in", url=url))
    thread = threading.Thread(target=lambda: _serve_until(server, answer), daemon=True)
    thread.start()
    open_browser(url)
    thread.join(timeout=600)
    server.server_close()
    if "code" not in answer:
        raise YouTubeError(msg("youtube.sign_in_failed", error=answer.get("error", "no answer from the browser")))
    token = _token_request(client, {"code": answer["code"], "code_verifier": verifier, "redirect_uri": redirect,
                                    "grant_type": "authorization_code"})
    _save_token(token)
    return token


def _serve_until(server: http.server.HTTPServer, answer: dict) -> None:
    server.timeout = 1
    deadline = time.time() + 600
    while not answer and time.time() < deadline:
        server.handle_request()


def access_token(report: Callable[[str], None], open_browser: Callable[[str], object] = webbrowser.open) -> str:
    """A valid access token: the saved one, refreshed if it expired, or a
    new sign-in."""
    try:
        token = json.loads(token_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        token = {}
    if token.get("access_token") and token.get("expires_at", 0) > time.time():
        return token["access_token"]
    if token.get("refresh_token"):
        try:
            fresh = _token_request(_client(), {"refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
            _save_token({**token, **fresh})
            return fresh["access_token"]
        except YouTubeError:
            pass  # (revoked or expired: sign in again)
    return authorize(report, open_browser)["access_token"]


# --- the API ------------------------------------------------------------------


@dataclass
class Api:
    token: str
    calls: list[str] = field(default_factory=list)

    def call(self, method: str, path: str, params: dict, body: Optional[dict] = None) -> dict:
        url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        status, _, reply = _http(method, url, headers, data)
        self.calls.append(f"{method} {path}")
        if status >= 300:
            raise YouTubeError(msg("youtube.api_failed", what=f"{method} {path}", status=status, error=_error_text(reply)))
        return json.loads(reply) if reply else {}

    def my_channel(self) -> tuple[str, str]:
        items = self.call("GET", "channels", {"part": "id,snippet", "mine": "true"}).get("items") or []
        if not items:
            raise YouTubeError(msg("youtube.no_channel"))
        return items[0]["id"], items[0]["snippet"]["title"]

    def upload(self, path: Path, resource: dict, on_progress: Callable[[int, int], None]) -> str:
        """The resumable upload, in CHUNK pieces; returns the new video's id."""
        size = path.stat().st_size
        status, headers, reply = _http(
            "POST", f"{UPLOAD_API}?uploadType=resumable&part=snippet,status",
            {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=UTF-8",
             "X-Upload-Content-Type": "video/*", "X-Upload-Content-Length": str(size)},
            json.dumps(resource).encode("utf-8"),
        )
        self.calls.append("POST upload")
        location = {k.lower(): v for k, v in headers.items()}.get("location")
        if status != 200 or not location:
            raise YouTubeError(msg("youtube.api_failed", what="upload", status=status, error=_error_text(reply)))
        sent = 0
        with path.open("rb") as fh:
            while True:
                piece = fh.read(CHUNK)
                end = sent + len(piece) - 1
                status, headers, reply = _http("PUT", location, {"Authorization": f"Bearer {self.token}",
                                                                 "Content-Length": str(len(piece)),
                                                                 "Content-Range": f"bytes {sent}-{end}/{size}"}, piece)
                sent += len(piece)
                on_progress(sent, size)
                if status in (200, 201):
                    return json.loads(reply)["id"]
                if status != 308 or not piece:
                    raise YouTubeError(msg("youtube.api_failed", what="upload", status=status, error=_error_text(reply)))

    def privacy_of(self, video_id: str) -> Optional[str]:
        items = self.call("GET", "videos", {"part": "status", "id": video_id}).get("items") or []
        return items[0]["status"]["privacyStatus"] if items else None

    def in_playlist(self, playlist: str, video_id: str) -> bool:
        reply = self.call("GET", "playlistItems", {"part": "id", "playlistId": playlist, "videoId": video_id})
        return bool(reply.get("items"))


def _resource(spec: YouTubeSpec) -> dict:
    snippet = {"title": spec.title, "description": spec.description, "categoryId": spec.category}
    if spec.tags:
        snippet["tags"] = list(spec.tags)
    return {"snippet": snippet, "status": {"privacyStatus": spec.privacy, "selfDeclaredMadeForKids": spec.made_for_kids}}


# --- publishing ---------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_lock(project_dir: Path) -> dict:
    try:
        return (yaml.safe_load((project_dir / LOCK_NAME).read_text(encoding="utf-8")) or {}).get("youtube") or {}
    except FileNotFoundError:
        return {}


def _write_lock(project_dir: Path, entry: dict) -> None:
    text = "# Written by `video-as-code publish`: what is on YouTube. Commit it; don't edit it by hand.\n"
    (project_dir / LOCK_NAME).write_text(text + yaml.safe_dump({"youtube": entry}, sort_keys=False, allow_unicode=True),
                                         encoding="utf-8")


@dataclass
class Plan:
    action: str  # "upload", "update" or "replace"
    video_id: Optional[str]
    size: int
    sha256: str


def plan(spec: YouTubeSpec, video: Path, project_dir: Path, replace: bool) -> Plan:
    if not video.is_file():
        raise YouTubeError(msg("youtube.no_video", path=video))
    lock = read_lock(project_dir)
    size, sha = video.stat().st_size, _sha256(video)
    if not lock.get("video_id"):
        return Plan("upload", None, size, sha)
    if lock.get("sha256") == sha:
        return Plan("update", lock["video_id"], size, sha)
    if not replace:
        raise YouTubeError(msg("youtube.changed", id=lock["video_id"]))
    return Plan("replace", lock["video_id"], size, sha)


def publish(
    spec: YouTubeSpec,
    video: Path,
    project_dir: Path,
    report: Callable[[str], None],
    dry_run: bool = False,
    replace: bool = False,
    yes: bool = False,
    ask: Callable[[str], str] = input,
    open_browser: Callable[[str], object] = webbrowser.open,
) -> Optional[str]:
    """Uploads `video`, or updates what's already up (see the module
    docstring); returns the video's id - None on a dry run, or when the
    user says no."""
    step = plan(spec, video, project_dir, replace)
    report(msg(f"youtube.plan_{step.action}", title=spec.title, privacy=spec.privacy, path=video,
               mb=round(step.size / 2**20), id=step.video_id))
    for playlist in spec.playlists:
        report(msg("youtube.plan_playlist", playlist=playlist))
    if dry_run:
        return None
    api = Api(access_token(report, open_browser))
    channel_id, channel_title = api.my_channel()
    if spec.channel and spec.channel != channel_id:
        raise YouTubeError(msg("youtube.wrong_channel", want=spec.channel, got=channel_id, title=channel_title))
    if step.action == "update":
        video_id = step.video_id
        api.call("PUT", "videos", {"part": "snippet,status"}, {"id": video_id, **_resource(spec)})
        report(msg("youtube.updated", id=video_id))
    else:
        question = msg("youtube.confirm", title=spec.title, channel=channel_title, privacy=spec.privacy)
        if not yes and ask(question).strip().lower() not in ("y", "yes", "s", "si", "sí"):
            report(msg("youtube.cancelled"))
            return None
        shown = [-1]

        def progress(sent: int, total: int) -> None:
            percent = sent * 100 // max(total, 1)
            if percent // 5 != shown[0] // 5:
                shown[0] = percent
                report(msg("youtube.uploading", bar="█" * (percent // 5) + "░" * (20 - percent // 5), percent=percent))

        video_id = api.upload(video, _resource(spec), progress)
        report(msg("youtube.uploaded", id=video_id, url=f"https://youtu.be/{video_id}"))
        if step.action == "replace":
            report(msg("youtube.replaced", old=step.video_id))
    _write_lock(project_dir, {"video_id": video_id, "channel": channel_id, "size": step.size, "sha256": step.sha256,
                              "published": time.strftime("%Y-%m-%dT%H:%M:%S")})
    for playlist in spec.playlists:
        if not api.in_playlist(playlist, video_id):
            api.call("POST", "playlistItems", {"part": "snippet"},
                     {"snippet": {"playlistId": playlist, "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
            report(msg("youtube.added_to_playlist", playlist=playlist))
    got = api.privacy_of(video_id)
    if spec.privacy != "private" and got == "private":
        # an API project Google hasn't audited: its uploads stay private
        report(msg("youtube.locked_private", want=spec.privacy, url=STUDIO_URL.format(id=video_id)))
    return video_id
