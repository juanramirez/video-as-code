"""`video-as-code docs` (docs.py): the README and docs/*.md as pages,
linked to the gallery's cards and back."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_as_code import docs, gallery

pytest.importorskip("markdown")


def test_reference_keys_get_anchors_once():
    body = docs._markdown("| Key | Meaning |\n|---|---|\n| `luma` | A shape. |\n\n| Key | x |\n|---|---|\n| `luma` | again |\n")
    body, keys = docs.key_anchors(body)
    assert keys == {"luma"} and body.count('id="key-luma"') == 1


def test_links_to_the_markdown_go_to_the_pages():
    body = docs._relink(docs._markdown("See `docs/project-yaml.md` and [the guide](docs/making-a-video.md#scenes)."))
    assert '<a href="project-yaml.html"><code>docs/project-yaml.md</code></a>' in body
    assert 'href="making-a-video.html#scenes"' in body



def test_pages_and_gallery_linked_both_ways(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "README.md").write_text("# video-as-code\n\nThe reference: `docs/project-yaml.md`.\n", encoding="utf-8")
    (source / "docs" / "project-yaml.md").write_text(
        "# Project YAML reference\n\n| Key | Meaning |\n|---|---|\n| `luma` | A shape. |\n| `notes` | Text. |\n", encoding="utf-8")
    seen = {}

    def fake_gallery(own, cache_root, on_progress, doc_links, docs_home, public, footer="", site_nav=""):
        seen.update(doc_links=doc_links, docs_home=docs_home, public=public)
        root = cache_root / gallery.GALLERY_DIR
        root.mkdir(parents=True)
        (root / gallery.DOC_CARDS).write_text(json.dumps({"luma": "card-lumas-heart"}), encoding="utf-8")
        (root / "index.html").write_text("", encoding="utf-8")
        return root / "index.html"

    monkeypatch.setattr(gallery, "build", fake_gallery)
    page = docs.build(cache_root=tmp_path / "cache", source=source)
    assert page == tmp_path / "cache" / "docs" / "index.html"
    assert seen["doc_links"] == {k: f"../docs/project-yaml.html#key-{k}" for k in ("luma", "notes")}
    assert seen["docs_home"] == "../docs/index.html"
    assert "<title>video-as-code - video-as-code</title>" in page.read_text(encoding="utf-8")
    reference = (page.parent / "project-yaml.html").read_text(encoding="utf-8")
    assert 'href="../gallery/index.html#card-lumas-heart"' in reference  # (luma has a card; notes none)
    assert reference.count("../gallery/index.html#card-") == 1
    assert 'href="project-yaml.html"' in page.read_text(encoding="utf-8")  # (the nav, and the README's mention)


def test_local_images_copied_next_to_the_pages(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (source / "README.md").write_text(
        '# <img src="docs/logo.svg" alt="" width="48"/> video-as-code\n\n'
        '<img src="https://example.com/remote.png"/>\n', encoding="utf-8")
    _fake_gallery(monkeypatch, {})
    page = docs.build(cache_root=tmp_path / "cache", source=source)
    assert (page.parent / "docs" / "logo.svg").read_text(encoding="utf-8") == "<svg/>"
    assert "<title>video-as-code - video-as-code</title>" in page.read_text(encoding="utf-8")


def test_repository_and_license_on_every_page(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "README.md").write_text("# video-as-code\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[project]\nname = "video-as-code"\nlicense = {text = "MIT"}\n\n'
        '[project.urls]\nRepository = "https://github.com/someone/video-as-code"\n', encoding="utf-8")
    seen: dict = {}

    def fake(own, cache_root, on_progress, doc_links, docs_home, public, footer="", site_nav=""):
        seen["footer"] = footer
        root = cache_root / gallery.GALLERY_DIR
        root.mkdir(parents=True)
        (root / gallery.DOC_CARDS).write_text("{}", encoding="utf-8")
        (root / "index.html").write_text("", encoding="utf-8")
        return root / "index.html"

    monkeypatch.setattr(gallery, "build", fake)
    page = docs.build(cache_root=tmp_path / "cache", source=source).read_text(encoding="utf-8")
    assert '<a class="repo" href="https://github.com/someone/video-as-code">' in page
    assert 'href="https://github.com/someone/video-as-code/blob/HEAD/LICENSE"' in page
    assert "<footer>video-as-code · " in page
    assert seen["footer"] and "LICENSE" in seen["footer"]  # (the gallery gets the same line)


def test_the_gallery_gets_the_docs_bar(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "README.md").write_text("# video-as-code\n", encoding="utf-8")
    (source / "docs" / "project-yaml.md").write_text("# Project YAML reference\n", encoding="utf-8")
    seen: dict = {}

    def fake(own, cache_root, on_progress, doc_links, docs_home, public, footer="", site_nav=""):
        seen["site_nav"] = site_nav
        root = cache_root / gallery.GALLERY_DIR
        root.mkdir(parents=True)
        (root / gallery.DOC_CARDS).write_text("{}", encoding="utf-8")
        (root / "index.html").write_text("", encoding="utf-8")
        return root / "index.html"

    monkeypatch.setattr(gallery, "build", fake)
    page = docs.build(cache_root=tmp_path / "cache", source=source).read_text(encoding="utf-8")
    assert '<nav class="site"><span class="pages"><a href="index.html" aria-current=page>video-as-code</a>' in page
    assert '<a href="../gallery/index.html">' in page
    bar = seen["site_nav"]
    assert '<a href="../docs/index.html">video-as-code</a>' in bar
    assert '<a href="../docs/project-yaml.html">Project YAML reference</a>' in bar
    assert '<a href="index.html" aria-current=page>' in bar  # (the gallery itself)


def test_a_long_title_short_in_the_bar(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "README.md").write_text("# video-as-code\n", encoding="utf-8")
    (source / "docs" / "reconstructing.md").write_text("# How to recover a YAML from an already-rendered video\n", encoding="utf-8")
    _fake_gallery(monkeypatch, {})
    page = (docs.build(cache_root=tmp_path / "cache", source=source).parent / "reconstructing.html").read_text(encoding="utf-8")
    assert '<a href="reconstructing.html" aria-current=page>How to recover a YAML</a>' in page
    assert "<title>How to recover a YAML from an already-rendered video - video-as-code</title>" in page
    assert '<button class="menu"' in page and '<span class="more">' in page


def test_without_a_pyproject_no_links(tmp_path: Path):
    assert docs._project(tmp_path) == (None, None) and docs._about(None, None) == ""


def test_without_a_checkout(tmp_path: Path):
    with pytest.raises(docs.KdenliveAsCodeError, match="README.md"):
        docs.build(cache_root=tmp_path, source=tmp_path / "nowhere")


def _fake_gallery(monkeypatch, seen: dict):
    def build(own, cache_root, on_progress, doc_links, docs_home, public, footer="", site_nav=""):
        seen.update(public=public, cache_root=cache_root)
        root = cache_root / gallery.GALLERY_DIR
        (root / "files").mkdir(parents=True, exist_ok=True)
        (root / "files" / "a.mp4").write_bytes(b"mp4")
        (root / "work").mkdir(exist_ok=True)  # (not published)
        (root / gallery.DOC_CARDS).write_text("{}", encoding="utf-8")
        (root / "index.html").write_text("gallery", encoding="utf-8")
        return root / "index.html"

    monkeypatch.setattr(gallery, "build", build)
    monkeypatch.setattr(gallery, "_melt_version", lambda: "melt 7.36.1")


def test_public_a_folder_to_publish(tmp_path: Path, monkeypatch):
    """Built apart, over generated pictures (public=True), and only the
    pages and the previews copied, with an index and a .nojekyll."""
    seen: dict = {}
    _fake_gallery(monkeypatch, seen)
    out = tmp_path / "site"
    page = docs.publish(out, cache_root=tmp_path / "cache")
    assert seen == {"public": True, "cache_root": tmp_path / "cache" / "public"}
    assert page == out / "index.html" and 'url=docs/index.html' in page.read_text(encoding="utf-8")
    assert (out / ".nojekyll").exists() and (out / "gallery" / "files" / "a.mp4").exists()
    assert not (out / "gallery" / "work").exists() and not (out / "gallery" / gallery.DOC_CARDS).exists()
    readme = (out / "docs" / "index.html").read_text(encoding="utf-8")
    assert "<footer>" in readme and "melt 7.36.1" in readme
    (out / ".git").write_text("gitdir: elsewhere", encoding="utf-8")  # (a worktree of gh-pages)
    (out / "CNAME").write_text("docs.example.org\n", encoding="utf-8")  # (its custom domain)
    (out / "docs" / "old.html").write_text("", encoding="utf-8")
    docs.publish(out, cache_root=tmp_path / "cache")  # (again: its own folder, replaced)
    assert (out / ".git").exists() and not (out / "docs" / "old.html").exists()
    assert (out / "CNAME").read_text(encoding="utf-8") == "docs.example.org\n"


def test_public_into_a_new_worktree(tmp_path: Path, monkeypatch):
    _fake_gallery(monkeypatch, {})
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (tmp_path / "pages" / "CNAME").write_text("docs.example.org\n", encoding="utf-8")
    docs.publish(tmp_path / "pages", cache_root=tmp_path / "cache")
    assert (tmp_path / "pages" / "docs" / "index.html").exists()


def test_public_wont_write_over_another_folder(tmp_path: Path, monkeypatch):
    _fake_gallery(monkeypatch, {})
    (tmp_path / "mine.txt").write_text("x", encoding="utf-8")
    with pytest.raises(docs.KdenliveAsCodeError, match="isn't empty"):
        docs.publish(tmp_path, cache_root=tmp_path / "cache")
    assert (tmp_path / "mine.txt").exists()
