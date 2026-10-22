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

    def fake_gallery(own, cache_root, on_progress, doc_links, docs_home):
        seen.update(doc_links=doc_links, docs_home=docs_home)
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
