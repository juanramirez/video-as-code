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

    def fake_gallery(own, cache_root, on_progress, doc_links, docs_home, public):
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


def test_without_a_checkout(tmp_path: Path):
    with pytest.raises(docs.KdenliveAsCodeError, match="README.md"):
        docs.build(cache_root=tmp_path, source=tmp_path / "nowhere")


def _fake_gallery(monkeypatch, seen: dict):
    def build(own, cache_root, on_progress, doc_links, docs_home, public):
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
    (out / "docs" / "old.html").write_text("", encoding="utf-8")
    docs.publish(out, cache_root=tmp_path / "cache")  # (again: its own folder, replaced)
    assert (out / ".git").exists() and not (out / "docs" / "old.html").exists()


def test_public_into_a_new_worktree(tmp_path: Path, monkeypatch):
    _fake_gallery(monkeypatch, {})
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    docs.publish(tmp_path / "pages", cache_root=tmp_path / "cache")
    assert (tmp_path / "pages" / "docs" / "index.html").exists()


def test_public_wont_write_over_another_folder(tmp_path: Path, monkeypatch):
    _fake_gallery(monkeypatch, {})
    (tmp_path / "mine.txt").write_text("x", encoding="utf-8")
    with pytest.raises(docs.KdenliveAsCodeError, match="isn't empty"):
        docs.publish(tmp_path, cache_root=tmp_path / "cache")
    assert (tmp_path / "mine.txt").exists()
