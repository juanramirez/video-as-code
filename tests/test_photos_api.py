"""photos_api.py: the Google Photos upload, against a fake HTTP layer (no network, no browser)."""
from __future__ import annotations

import io
import json
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from video_as_code import photos_api
from video_as_code.photos_api import Api, PhotosApiError


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("VIDEO_AS_CODE_PHOTOS_CLIENT", str(tmp_path / "client.json"))
    return tmp_path


def write_client(tmp_path, data=None):
    (tmp_path / "client.json").write_text(json.dumps(data or {"installed": {"client_id": "id", "client_secret": "secret"}}))


class FakeHttp:
    """Replaces photos_api._http: answers from a queue and records every request."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, headers, payload = answer
        return status, headers, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def use(monkeypatch, *answers):
    fake = FakeHttp(*answers)
    monkeypatch.setattr(photos_api, "_http", fake)
    return fake


# --- the OAuth client file --------------------------------------------------

def test_client_file_can_be_pointed_elsewhere(tmp_path):
    assert photos_api.client_file() == tmp_path / "client.json"


def test_client_file_defaults_under_the_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("VIDEO_AS_CODE_PHOTOS_CLIENT")
    assert photos_api.client_file() == tmp_path / "config" / "video-as-code" / "photos_client.json"


@pytest.mark.parametrize("wrapper", ["installed", "web", None])
def test_client_reads_every_shape_google_downloads(tmp_path, wrapper):
    body = {"client_id": "id", "client_secret": "secret", "token_uri": "https://t"}
    write_client(tmp_path, {wrapper: body} if wrapper else body)
    client = photos_api._client()
    assert client["client_id"] == "id" and client["client_secret"] == "secret" and client["token_uri"] == "https://t"
    assert client["auth_uri"].startswith("https://accounts.google.com")


def test_client_missing_says_where_to_get_one():
    with pytest.raises(PhotosApiError, match="console.cloud.google.com"):
        photos_api.check_client()


@pytest.mark.parametrize("content", ["not json", json.dumps({"installed": {"client_secret": "x"}})])
def test_client_unreadable_or_without_an_id(tmp_path, content):
    (tmp_path / "client.json").write_text(content)
    with pytest.raises(PhotosApiError, match="client.json"):
        photos_api.check_client()


# --- HTTP and errors --------------------------------------------------------

class Response:
    def __init__(self, status=200, body=b"ok"):
        self.status, self.headers, self._body = status, {"X-Test": "1"}, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_returns_status_headers_and_body(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Response(200, b"hello"))
    assert photos_api._http("GET", "https://x") == (200, {"X-Test": "1"}, b"hello")


def test_http_hands_back_an_error_status_instead_of_raising(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.HTTPError("https://x", 403, "no", {"X-Why": "scope"}, io.BytesIO(b"denied"))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert photos_api._http("GET", "https://x") == (403, {"X-Why": "scope"}, b"denied")


def test_http_network_failure_is_a_photos_error(monkeypatch):
    def down(request, timeout):
        raise OSError("unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(PhotosApiError, match="unreachable"):
        photos_api._http("GET", "https://x")


def test_error_message_from_google_json_or_plain_text():
    assert photos_api._error(json.dumps({"error": {"message": "quota"}}).encode()) == "quota"
    assert photos_api._error(b"<html>bad gateway</html>") == "<html>bad gateway</html>"
    assert photos_api._error(b"[]") == "[]"
    assert len(photos_api._error(b"x" * 1000)) == 300


# --- tokens -----------------------------------------------------------------

def test_token_is_saved_private(config):
    photos_api._save_token({"access_token": "a"})
    path = config / "config" / "video-as-code" / "photos_token.json"
    assert json.loads(path.read_text()) == {"access_token": "a"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_token_request_adds_an_expiry(monkeypatch, config):
    write_client(config)
    fake = use(monkeypatch, (200, {}, {"access_token": "a", "expires_in": 3600}))
    before = time.time()
    token = photos_api._token_request(photos_api._client(), {"grant_type": "refresh_token", "refresh_token": "r"})
    assert before + 3500 < token["expires_at"] <= time.time() + 3540
    sent = urllib.parse.parse_qs(fake.calls[0]["body"].decode())
    assert sent["client_id"] == ["id"] and sent["grant_type"] == ["refresh_token"]


def test_token_request_refused(monkeypatch, config):
    write_client(config)
    use(monkeypatch, (400, {}, {"error": {"message": "invalid_grant"}}))
    with pytest.raises(PhotosApiError, match="invalid_grant"):
        photos_api._token_request(photos_api._client(), {})


def stored(config, token):
    path = config / "config" / "video-as-code" / "photos_token.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(token))


def test_access_token_reuses_a_live_one_without_any_request(monkeypatch, config):
    stored(config, {"access_token": "live", "expires_at": time.time() + 600})
    fake = use(monkeypatch)
    assert photos_api.access_token(print) == "live"
    assert fake.calls == []


def test_access_token_refreshes_an_expired_one_and_keeps_the_refresh_token(monkeypatch, config):
    write_client(config)
    stored(config, {"access_token": "old", "expires_at": 0, "refresh_token": "r"})
    use(monkeypatch, (200, {}, {"access_token": "new", "expires_in": 3600}))
    assert photos_api.access_token(print) == "new"
    saved = json.loads((config / "config" / "video-as-code" / "photos_token.json").read_text())
    assert saved["access_token"] == "new" and saved["refresh_token"] == "r"


def test_access_token_signs_in_again_when_the_refresh_is_refused(monkeypatch, config):
    write_client(config)
    stored(config, {"access_token": "old", "expires_at": 0, "refresh_token": "revoked"})
    use(monkeypatch, (400, {}, {"error": {"message": "revoked"}}))
    monkeypatch.setattr(photos_api, "_authorize", lambda report: {"access_token": "signed-in"})
    assert photos_api.access_token(print) == "signed-in"


def test_access_token_without_any_token_signs_in(monkeypatch, config):
    write_client(config)
    monkeypatch.setattr(photos_api, "_authorize", lambda report: {"access_token": "first"})
    assert photos_api.access_token(print) == "first"


def test_authorize_runs_the_browser_round_trip(monkeypatch, config):
    """The loopback server gets the browser's redirect; the code goes to the token endpoint with the PKCE verifier."""
    write_client(config)
    said, opened = [], {}

    def browser(url):
        opened["url"] = url
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        # a stray request with a wrong state must not be taken for the answer
        urllib.request.urlopen(query["redirect_uri"] + "?state=wrong&code=stray").read()
        urllib.request.urlopen(query["redirect_uri"] + "?state=" + query["state"] + "&code=the-code").read()
        return True

    fake = use(monkeypatch, (200, {}, {"access_token": "tok", "refresh_token": "r", "expires_in": 3600}))
    monkeypatch.setattr(photos_api.webbrowser, "open", browser)
    token = photos_api._authorize(said.append)

    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(opened["url"]).query))
    assert query["scope"] == photos_api.SCOPES and query["code_challenge_method"] == "S256"
    assert opened["url"] in said[0]
    sent = urllib.parse.parse_qs(fake.calls[0]["body"].decode())
    assert sent["code"] == ["the-code"] and sent["redirect_uri"] == [query["redirect_uri"]]
    import base64, hashlib
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
    assert challenge == query["code_challenge"]
    assert token["access_token"] == "tok"
    assert (config / "config" / "video-as-code" / "photos_token.json").exists()


# --- the API ----------------------------------------------------------------

def test_call_sends_the_bearer_token_and_json(monkeypatch):
    fake = use(monkeypatch, (200, {}, {"id": "album1"}))
    assert Api("tok").create_album("My album") == {"id": "album1"}
    call = fake.calls[0]
    assert call["method"] == "POST" and call["url"] == photos_api.API + "/albums"
    assert call["headers"]["Authorization"] == "Bearer tok" and call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["body"]) == {"album": {"title": "My album"}}


def test_call_without_a_reply_body(monkeypatch):
    use(monkeypatch, (200, {}, b""))
    assert Api("tok").call("GET", "albums") == {}


def test_call_error_status(monkeypatch):
    use(monkeypatch, (403, {}, {"error": {"message": "insufficient scope"}}))
    with pytest.raises(PhotosApiError, match=r"HTTP 403.*insufficient scope"):
        Api("tok").call("GET", "albums")


SESSION = "https://upload.example/session"


def started():
    return 200, {"X-Goog-Upload-URL": SESSION}, b""


def test_upload_sends_the_file_in_chunks_and_finalizes_the_last(monkeypatch):
    monkeypatch.setattr(photos_api, "MAX_CHUNK", 4)
    fake = use(monkeypatch, started(), (200, {}, b""), (200, {}, b""), (200, {}, b"UPLOAD-TOKEN\n"))
    assert Api("tok").upload_stream("pic.jpg", 10, io.BytesIO(b"0123456789")) == "UPLOAD-TOKEN"
    start, *chunks = fake.calls
    assert start["url"] == photos_api.UPLOADS
    assert start["headers"]["X-Goog-Upload-Content-Type"] == "image/jpeg"
    assert start["headers"]["X-Goog-Upload-Raw-Size"] == "10"
    assert [(c["url"], c["body"], c["headers"]["X-Goog-Upload-Offset"], c["headers"]["X-Goog-Upload-Command"])
            for c in chunks] == [
        (SESSION, b"0123", "0", "upload"), (SESSION, b"4567", "4", "upload"), (SESSION, b"89", "8", "upload, finalize")]


def test_upload_reads_the_session_url_whatever_its_case(monkeypatch):
    use(monkeypatch, (200, {"x-goog-upload-url": SESSION}, b""), (200, {}, b"T"))
    assert Api("tok").upload_stream("a.bin", 1, io.BytesIO(b"x"), mime="application/x-test") == "T"


def test_upload_of_a_path_uses_its_name_and_size(monkeypatch, tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    fake = use(monkeypatch, started(), (200, {}, b"T"))
    assert Api("tok").upload(path) == "T"
    assert fake.calls[0]["headers"]["X-Goog-Upload-Content-Type"] == "video/mp4"
    assert fake.calls[1]["body"] == b"video"


def test_upload_refused_at_the_start(monkeypatch):
    use(monkeypatch, (401, {}, {"error": {"message": "bad token"}}))
    with pytest.raises(PhotosApiError, match="bad token"):
        Api("tok").upload_stream("a.jpg", 1, io.BytesIO(b"x"))


def test_upload_without_a_session_url(monkeypatch):
    use(monkeypatch, (200, {}, b""))
    with pytest.raises(PhotosApiError):
        Api("tok").upload_stream("a.jpg", 1, io.BytesIO(b"x"))


def test_upload_chunk_refused(monkeypatch):
    use(monkeypatch, started(), (500, {}, {"error": {"message": "backend"}}))
    with pytest.raises(PhotosApiError, match="backend"):
        Api("tok").upload_stream("a.jpg", 1, io.BytesIO(b"x"))


def test_upload_from_a_stream_shorter_than_announced(monkeypatch):
    use(monkeypatch, started())
    with pytest.raises(PhotosApiError, match="a.jpg.*0 of 5"):
        Api("tok").upload_stream("a.jpg", 5, io.BytesIO(b""))


def batch_reply(names, code=0):
    return 200, {}, {"newMediaItemResults": [
        {"mediaItem": {"filename": n}, "status": {"code": code, "message": "bad file"} if code else {}} for n in names]}


def test_add_to_album_batches_by_fifty(monkeypatch):
    uploads = [(f"f{i}.jpg", f"tok{i}") for i in range(120)]
    fake = use(monkeypatch, batch_reply([n for n, _ in uploads[:50]]), batch_reply([n for n, _ in uploads[50:100]]),
               batch_reply([n for n, _ in uploads[100:]]))
    results = Api("tok").add_to_album("album1", uploads)
    assert [r["filename"] for r in results] == [n for n, _ in uploads]
    bodies = [json.loads(c["body"]) for c in fake.calls]
    assert [len(b["newMediaItems"]) for b in bodies] == [50, 50, 20]
    assert all(b["albumId"] == "album1" for b in bodies)
    assert bodies[0]["newMediaItems"][0] == {"simpleMediaItem": {"fileName": "f0.jpg", "uploadToken": "tok0"}}
    assert fake.calls[0]["url"].endswith("/mediaItems:batchCreate")


def test_add_to_album_an_item_google_refused(monkeypatch):
    use(monkeypatch, batch_reply(["a.jpg"], code=3))
    with pytest.raises(PhotosApiError, match="a.jpg.*bad file"):
        Api("tok").add_to_album("album1", [("a.jpg", "t")])


def test_add_to_album_fewer_items_than_uploads(monkeypatch):
    use(monkeypatch, batch_reply(["a.jpg"]))
    with pytest.raises(PhotosApiError, match="1 of 2"):
        Api("tok").add_to_album("album1", [("a.jpg", "t1"), ("b.jpg", "t2")])
