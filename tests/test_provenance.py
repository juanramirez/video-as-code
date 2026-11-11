"""provenance.py / `clean`: files go only after their sources answered."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from video_as_code import cli, gphotos, provenance, sources_lock
from video_as_code.provenance import Verdict


class FakeNet:
    """Answers from a set of names/urls that are 'still there'."""

    def __init__(self, items=(), urls=()):
        self.items, self.urls = set(items), set(urls)
        self.asked: list[tuple] = []

    def album_item(self, album_url, name, media_id, size):
        self.asked.append(("item", album_url, name, media_id))
        return Verdict(name in self.items, f"item {name}")

    def url(self, url, size):
        self.asked.append(("url", url))
        return Verdict(url in self.urls, f"url {url}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        "project:\n  sources:\n    album: https://photos.app.goo.gl/x\n"
        "    reference: {url: 'https://youtu.be/ref', path: reference/ref.mp4}\n",
        encoding="utf-8",
    )
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "make.sh").write_text("#!/bin/sh\n")
    return yaml_path


def _put(base: Path, rel: str, data: bytes = b"x") -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_register_round_trips_merges_and_rejects_outside(tmp_path: Path):
    provenance.register(tmp_path, ".work/a", [{"item": "A.mov"}], command="make a")
    provenance.register(tmp_path, tmp_path / ".work" / "a", [{"item": "A.mov"}, {"url": "https://x/y"}], recipe="r.sh")
    entries = provenance.read_registry(tmp_path)
    assert entries == [{"path": ".work/a", "sources": [{"item": "A.mov"}, {"url": "https://x/y"}],
                        "recipe": "r.sh", "command": "make a"}]
    with pytest.raises(provenance.ProvenanceError):
        provenance.register(tmp_path, "../elsewhere", [{"url": "u"}])
    with pytest.raises(provenance.ProvenanceError):
        provenance.register(tmp_path, ".", [{"url": "u"}])
    with pytest.raises(provenance.ProvenanceError):
        provenance.register(tmp_path, "a", [{"item": "x", "url": "y"}])  # two kinds in one source


def test_registry_with_an_unknown_key_is_refused(tmp_path: Path):
    (tmp_path / "provenance.yaml").write_text(yaml.safe_dump({"files": [{"path": "a", "sources": [{"itme": "x"}]}]}))
    with pytest.raises(provenance.ProvenanceError):
        provenance.read_registry(tmp_path)


def test_clean_deletes_only_what_every_source_confirmed(project: Path):
    base = project.parent
    _put(base, ".work/vids/A.mov", b"aaa")
    _put(base, ".work/vids/B.mov", b"bbbb")
    provenance.register(base, ".work/vids", [{"item": "{name}"}], recipe="recipes/make.sh")
    net = FakeNet(items={"A.mov"})  # B is gone from the album
    report = provenance.clean(project, net=net)
    assert not (base / ".work/vids/A.mov").exists()
    assert (base / ".work/vids/B.mov").read_bytes() == b"bbbb"
    by_name = {r.candidate.path.name: r for r in report.results}
    assert by_name["A.mov"].ok and by_name["A.mov"].removed
    assert not by_name["B.mov"].ok and "B.mov" in by_name["B.mov"].reasons[0]
    # the album asked is the project's, and the name each file's own
    assert ("item", None, "B.mov", None) in net.asked


def test_dry_run_deletes_nothing(project: Path):
    base = project.parent
    _put(base, ".work/f.bin")
    provenance.register(base, ".work/f.bin", [{"url": "https://x/f"}], command="curl")
    report = provenance.clean(project, dry_run=True, net=FakeNet(urls={"https://x/f"}))
    assert report.results[0].ok and not report.results[0].removed
    assert (base / ".work/f.bin").exists()


def test_a_file_without_recipe_sources_or_with_a_missing_recipe_stays(project: Path):
    base = project.parent
    for name in ("norecipe", "badrecipe", "nosources"):
        _put(base, f".work/{name}")
    provenance.register(base, ".work/norecipe", [{"url": "u"}])
    provenance.register(base, ".work/badrecipe", [{"url": "u"}], recipe="recipes/nope.sh")
    provenance.register(base, ".work/nosources", [], command="x")
    report = provenance.clean(project, net=FakeNet(urls={"u"}))
    assert not any(r.ok for r in report.results)
    assert all((base / ".work" / n).exists() for n in ("norecipe", "badrecipe", "nosources"))
    reasons = {r.candidate.path.name: r.reasons[0] for r in report.results}
    assert "recipe" in reasons["norecipe"] and "nope.sh" in reasons["badrecipe"] and "sources" in reasons["nosources"]


def test_every_source_must_answer(project: Path):
    base = project.parent
    _put(base, ".work/two.bin")
    provenance.register(base, ".work/two.bin", [{"url": "https://a"}, {"url": "https://b"}], command="x")
    report = provenance.clean(project, net=FakeNet(urls={"https://a"}))
    assert not report.results[0].ok and (base / ".work/two.bin").exists()


def test_unregistered_work_files_are_reported_and_kept(project: Path):
    base = project.parent
    _put(base, ".work/notes/a.json", b"{}")
    _put(base, ".work/reg.bin")
    provenance.register(base, ".work/reg.bin", [{"url": "u"}], command="x")
    report = provenance.clean(project, net=FakeNet(urls={"u"}))
    assert report.unregistered == {".work/notes": (1, 2)}
    assert (base / ".work/notes/a.json").exists()


def test_only_restricts_to_the_given_paths(project: Path):
    base = project.parent
    _put(base, ".work/a/1")
    _put(base, ".work/b/1")
    provenance.register(base, ".work/a", [{"url": "u"}], command="x")
    provenance.register(base, ".work/b", [{"url": "u"}], command="x")
    provenance.clean(project, only=[".work/a"], net=FakeNet(urls={"u"}))
    assert not (base / ".work/a/1").exists() and (base / ".work/b/1").exists()


def test_a_deeper_entry_overrides_its_folders_entry_and_emptied_folders_go(project: Path):
    base = project.parent
    _put(base, ".work/d/keep.bin")
    _put(base, ".work/d/sub/x.bin")
    provenance.register(base, ".work/d", [{"url": "good"}], command="x")
    provenance.register(base, ".work/d/keep.bin", [{"url": "bad"}], command="x")
    provenance.clean(project, net=FakeNet(urls={"good"}))
    assert (base / ".work/d/keep.bin").exists()
    assert not (base / ".work/d/sub").exists()  # emptied, so removed; the entry's own folder stays


def test_reference_link_and_reference_file_sources(project: Path):
    base = project.parent
    sources_lock.write(base, {}, links={"music": {"url": "https://youtu.be/m", "ext": ".m4a"}})
    _put(base, ".work/frames/1.png")
    _put(base, ".work/song/1.wav")
    provenance.register(base, ".work/frames", [{"file": "reference/ref.mp4"}], recipe="recipes/make.sh")
    provenance.register(base, ".work/song", [{"link": "music"}], command="x")
    net = FakeNet(urls={"https://youtu.be/ref", "https://youtu.be/m"})
    report = provenance.clean(project, net=net)
    assert all(r.removed for r in report.results)
    assert ("url", "https://youtu.be/ref") in net.asked  # the reference file is gone: its url was checked
    net = FakeNet()
    _put(base, ".work/frames/1.png")
    assert not provenance.clean(project, net=net).results[0].ok


def test_a_file_source_that_is_gone_needs_to_be_regenerable_itself(project: Path):
    base = project.parent
    _put(base, ".work/b.bin")
    provenance.register(base, ".work/b.bin", [{"file": ".work/a.bin"}], command="x")
    assert not provenance.clean(project, dry_run=True, net=FakeNet()).results[0].ok  # a.bin: no record
    provenance.register(base, ".work/a.bin", [{"url": "u"}], command="y")  # recorded, but a.bin isn't on disk
    report = provenance.clean(project, dry_run=True, net=FakeNet(urls={"u"}))
    assert report.results[0].ok
    report = provenance.clean(project, dry_run=True, net=FakeNet())
    assert not report.results[0].ok
    _put(base, ".work/a.bin", b"data")  # present on disk: with a sha256, it has to be that file
    provenance.register(base, ".work/b.bin", [{"file": ".work/a.bin", "sha256": "0" * 64}])
    bad = [r for r in provenance.clean(project, dry_run=True, net=FakeNet(urls={"u"})).results
           if r.candidate.path.name == "b.bin"]
    assert not bad[0].ok and "sha256" in bad[0].reasons[0]


def test_sha256_pins_a_present_file(project: Path):
    base = project.parent
    src = _put(base, "assets/src.bin", b"data")
    _put(base, ".work/out.bin")
    provenance.register(base, ".work/out.bin", [{"file": "assets/src.bin", "sha256": gphotos.sha256_of(src)}], command="x")
    assert provenance.clean(project, dry_run=True, net=FakeNet()).results[0].ok
    src.write_bytes(b"changed")
    assert not provenance.clean(project, dry_run=True, net=FakeNet()).results[0].ok


def _sidecar(base: Path, rel: str, **fields) -> Path:
    out = _put(base, rel)
    (out.parent / (out.name + ".input.json")).write_text(json.dumps(fields), encoding="utf-8")
    return out


def test_derived_files_are_traced_through_their_sidecar_sha_to_the_lock(project: Path):
    base = project.parent
    sources_lock.write(base, {"IMG_1.mp4": {"media_id": "m1", "size": 3, "sha256": "aa"}})
    out = _sidecar(base, "assets/.derived/c-0123456789.mp4", input="20250101_c.mp4", sha256="aa")
    unknown = _sidecar(base, "assets/.derived/d-0123456789.mp4", input="d.mp4", sha256="zz")
    net = FakeNet(items={"IMG_1.mp4"})
    report = provenance.clean(project, net=net)
    assert not out.exists() and not (out.parent / (out.name + ".input.json")).exists()
    assert ("item", None, "IMG_1.mp4", "m1") in net.asked  # pinned by the lock's media id
    assert unknown.exists()
    bad = next(r for r in report.results if r.candidate.path == unknown)
    assert "d.mp4" in bad.reasons[0]


def test_a_derived_from_a_derived_follows_the_chain(project: Path):
    base = project.parent
    sources_lock.write(base, {"IMG_1.mp4": {"media_id": "m1", "sha256": "aa"}})
    _sidecar(base, "assets/.derived/a-0123456789.mp4", input="x.mp4", sha256="aa")
    _sidecar(base, "assets/.derived/b-0123456789.mp4", input="a-0123456789.mp4", sha256="bb")
    assert all(r.ok for r in provenance.clean(project, dry_run=True, net=FakeNet(items={"IMG_1.mp4"})).results)
    # the album answers no more: `a` stays, and `b` - made from a file that IS on disk - may go
    results = {r.candidate.path.name: r.ok for r in provenance.clean(project, dry_run=True, net=FakeNet()).results}
    assert results == {"a-0123456789.mp4": False, "b-0123456789.mp4": True}


def test_nothing_registered_nothing_deleted(project: Path):
    base = project.parent
    _put(base, ".work/x.bin")
    assert provenance.clean(project, net=FakeNet()).results == []
    assert (base / ".work/x.bin").exists()


def test_cli_provenance_then_clean(project: Path, monkeypatch, capsys):
    base = project.parent
    _put(base, ".work/enhance/001.png", b"12345")
    monkeypatch.chdir(base)  # the CLI takes paths as given from where it runs
    assert cli.main(["provenance", str(project), ".work/enhance", "--reference", "--recipe", "recipes/make.sh"]) == 0
    monkeypatch.setattr(provenance, "Net", lambda *a, **k: FakeNet(urls={"https://youtu.be/ref"}))
    assert cli.main(["clean", str(project), "--dry-run"]) == 0
    out = capsys.readouterr()
    assert ".work/enhance" in out.out and "Would free" in out.out and "tomorrow" in out.err
    assert (base / ".work/enhance/001.png").exists()
    assert cli.main(["clean", str(project)]) == 1  # not a terminal: refuses to ask, deletes nothing
    assert (base / ".work/enhance/001.png").exists()
    assert cli.main(["clean", str(project), "--yes-i-am-sure"]) == 0
    assert not (base / ".work/enhance/001.png").exists()


def _ask(monkeypatch, answer, locale="en"):
    from video_as_code import i18n
    monkeypatch.setattr(i18n, "LOCALE", locale)
    monkeypatch.setattr("sys.stdin", type("TTY", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)


@pytest.mark.parametrize("answer,locale,deleted", [
    ("Yes, I am sure", "en", True), ("yes, i am sure", "en", True),
    ("yes", "en", False), ("", "en", False), ("y", "en", False),
    ("Sí, estoy seguro", "es", True), ("si, estoy seguro", "es", True), ("Yes, I am sure", "es", False),
])
def test_clean_asks_for_the_typed_phrase_in_the_locale(project: Path, monkeypatch, capsys, answer, locale, deleted):
    base = project.parent
    _put(base, ".work/f.bin")
    provenance.register(base, ".work/f.bin", [{"url": "u"}], command="x")
    monkeypatch.setattr(provenance, "Net", lambda *a, **k: FakeNet(urls={"u"}))
    _ask(monkeypatch, answer, locale)
    assert cli.main(["clean", str(project)]) == (0 if deleted else 1)
    assert (not (base / ".work/f.bin").exists()) == deleted
    err = capsys.readouterr().err
    assert ("NO COPY OF THESE FILES" if locale == "en" else "NO SE HA LOCALIZADO NINGUNA COPIA") in err


def test_dry_run_never_asks(project: Path, monkeypatch):
    base = project.parent
    _put(base, ".work/f.bin")
    provenance.register(base, ".work/f.bin", [{"url": "u"}], command="x")
    monkeypatch.setattr(provenance, "Net", lambda *a, **k: FakeNet(urls={"u"}))
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))
    assert cli.main(["clean", str(project), "--dry-run"]) == 0


def test_cli_keeps_and_explains_when_a_source_cant_be_checked(project: Path, monkeypatch, capsys):
    base = project.parent
    _put(base, ".work/a.bin")
    provenance.register(base, ".work/a.bin", [{"url": "https://gone"}], command="x")
    monkeypatch.setattr(provenance, "Net", lambda *a, **k: FakeNet())
    assert cli.main(["clean", str(project)]) == 0
    out = capsys.readouterr().out
    assert "kept" in out and "https://gone" in out and (base / ".work/a.bin").exists()


# --- Net itself, with the network replaced -------------------------------------

class _Response:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_net_album_item_by_media_id_and_size(tmp_path: Path, monkeypatch):
    item = gphotos.AlbumItem("m1", "https://x/m1", True, 1, 1, 0)
    monkeypatch.setattr(gphotos, "list_album", lambda url, on_progress=None: [item])
    monkeypatch.setattr(gphotos, "_open", lambda request: _Response({"Content-Length": "10"}))
    net = provenance.Net("https://album", tmp_path)
    assert net.album_item(None, "A.mov", "m1", 10).ok
    assert "bytes" in net.album_item(None, "A.mov", "m1", 11).detail  # changed since recorded
    assert not net.album_item(None, "A.mov", "gone", None).ok
    assert not provenance.Net(None, tmp_path).album_item(None, "A.mov", "m1", None).ok  # no album at all


def test_net_url_failures_are_verdicts_not_exceptions(tmp_path: Path, monkeypatch):
    def refuse(request):
        raise gphotos.GPhotosError("offline")

    monkeypatch.setattr(gphotos, "_open", refuse)
    net = provenance.Net(None, tmp_path)
    verdict = net.url("https://example.com/f.mp4", None)
    assert not verdict.ok and "offline" in verdict.detail
    monkeypatch.setattr(gphotos, "_open", lambda r: _Response({"Content-Type": "text/html"}))
    assert not net.url("https://drive.google.com/file/d/abc/view", None).ok  # a page, not the file
