"""Create an app-owned Google Photos album and upload selected local files.

The Library API can only add items uploaded by this app to albums created by
this app. Sharing that album remains a user action in Google Photos.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from .errors import KdenliveAsCodeError
from .i18n import msg

API = "https://photoslibrary.googleapis.com/v1"
UPLOADS = API + "/uploads"
SCOPES = "https://www.googleapis.com/auth/photoslibrary.appendonly"
CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
MAX_CHUNK = 8 * 1024 * 1024


class PhotosApiError(KdenliveAsCodeError):
    pass


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "video-as-code"


def client_file() -> Path:
    return Path(os.environ.get("VIDEO_AS_CODE_PHOTOS_CLIENT", _config_dir() / "photos_client.json")).expanduser()


def _http(method: str, url: str, headers: dict | None = None, body: bytes | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except OSError as exc:
        raise PhotosApiError(msg("photos_api.network", error=exc)) from exc


def _error(body: bytes) -> str:
    try:
        return json.loads(body).get("error", {}).get("message", body.decode("utf-8", "replace")[:300])
    except (ValueError, AttributeError):
        return body.decode("utf-8", "replace")[:300]


def _client() -> dict:
    path = client_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw.get("installed") or raw.get("web") or raw
        return {"client_id": data["client_id"], "client_secret": data.get("client_secret", ""),
                "auth_uri": data.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": data.get("token_uri", "https://oauth2.googleapis.com/token")}
    except FileNotFoundError:
        raise PhotosApiError(msg("photos_api.no_client", path=path, url=CLOUD_CREDENTIALS_URL)) from None
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        raise PhotosApiError(msg("photos_api.bad_client", path=path, error=exc)) from exc


def check_client() -> None:
    """Validate local OAuth setup before starting an expensive match pass."""
    _client()


def _save_token(token: dict) -> None:
    path = _config_dir() / "photos_token.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(token, stream)
    tmp.replace(path)


def _token_request(client: dict, fields: dict) -> dict:
    body = urllib.parse.urlencode({"client_id": client["client_id"], "client_secret": client["client_secret"], **fields}).encode()
    status, _, data = _http("POST", client["token_uri"], {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if status != 200:
        raise PhotosApiError(msg("photos_api.auth_error", error=_error(data)))
    token = json.loads(data)
    token["expires_at"] = time.time() + float(token.get("expires_in", 3600)) - 60
    return token


def _authorize(report: Callable[[str], None]) -> dict:
    client = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, answer = secrets.token_urlsafe(16), {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if query.get("state") == state:
                answer.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg("photos_api.browser_done").encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = client["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent",
    })
    report(msg("photos_api.sign_in", url=url))
    thread = threading.Thread(target=lambda: _serve(server, answer), daemon=True)
    thread.start()
    webbrowser.open(url)
    thread.join(timeout=600)
    server.server_close()
    if "code" not in answer:
        raise PhotosApiError(msg("photos_api.auth_error", error=answer.get("error", "no browser response")))
    token = _token_request(client, {"code": answer["code"], "code_verifier": verifier,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"})
    _save_token(token)
    return token


def _serve(server, answer):
    server.timeout = 1
    deadline = time.time() + 600
    while not answer and time.time() < deadline:
        server.handle_request()


def access_token(report: Callable[[str], None]) -> str:
    path = _config_dir() / "photos_token.json"
    try:
        token = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        token = {}
    if token.get("access_token") and token.get("expires_at", 0) > time.time():
        return token["access_token"]
    client = _client()
    if token.get("refresh_token"):
        try:
            fresh = _token_request(client, {"refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
            _save_token({**token, **fresh})
            return fresh["access_token"]
        except PhotosApiError:
            pass
    return _authorize(report)["access_token"]


class Api:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        status, _, reply = _http(method, API + "/" + path, headers, data)
        if status >= 300:
            raise PhotosApiError(msg("photos_api.api_error", status=status, error=_error(reply)))
        return json.loads(reply) if reply else {}

    def create_album(self, title: str) -> dict:
        return self.call("POST", "albums", {"album": {"title": title}})

    def upload(self, path: Path) -> str:
        with path.open("rb") as stream:
            return self.upload_stream(path.name, path.stat().st_size, stream)

    def upload_stream(self, filename: str, size: int, stream, mime: str | None = None) -> str:
        mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Length": "0",
                   "X-Goog-Upload-Command": "start", "X-Goog-Upload-Content-Type": mime,
                   "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Raw-Size": str(size)}
        status, response_headers, body = _http("POST", UPLOADS, headers)
        session_url = response_headers.get("X-Goog-Upload-URL") or response_headers.get("x-goog-upload-url")
        if status != 200 or not session_url:
            raise PhotosApiError(msg("photos_api.api_error", status=status, error=_error(body)))
        offset = 0
        token = ""
        while offset < size:
            chunk = stream.read(min(MAX_CHUNK, size - offset))
            if not chunk:
                raise PhotosApiError(msg("photos_api.stream_ended", filename=filename, offset=offset, size=size))
            final = offset + len(chunk) == size
            headers = {"Authorization": f"Bearer {self.token}", "Content-Length": str(len(chunk)),
                       "X-Goog-Upload-Command": "upload, finalize" if final else "upload",
                       "X-Goog-Upload-Offset": str(offset)}
            status, _, body = _http("POST", session_url, headers, chunk)
            if status != 200:
                raise PhotosApiError(msg("photos_api.api_error", status=status, error=_error(body)))
            if final:
                token = body.decode("utf-8").strip()
            offset += len(chunk)
        return token

    def add_to_album(self, album_id: str, uploads: list[tuple[str, str]]) -> list[dict]:
        results: list[dict] = []
        for start in range(0, len(uploads), 50):
            chunk = uploads[start:start + 50]
            response = self.call("POST", "mediaItems:batchCreate", {
                "albumId": album_id,
                "newMediaItems": [{"simpleMediaItem": {"fileName": filename, "uploadToken": token}}
                                   for filename, token in chunk],
            })
            for item in response.get("newMediaItemResults", []):
                if item.get("status", {}).get("code", 0):
                    raise PhotosApiError(msg("photos_api.item_error", name=item.get("mediaItem", {}).get("filename", "?"),
                                             error=item["status"].get("message", "unknown error")))
                results.append(item.get("mediaItem", {}))
        if len(results) != len(uploads):
            raise PhotosApiError(msg("photos_api.result_count", expected=len(uploads), actual=len(results)))
        return results
