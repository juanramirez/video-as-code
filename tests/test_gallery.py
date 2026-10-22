"""gallery.py: every catalog entry is a valid project, and the page is
built from cached renders - against stand-ins (conftest.py's
openshot_transitions, a small catalog) so it stays quick."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_as_code import cli, gallery
from video_as_code.schema import load_and_resolve


@pytest.fixture(scope="module")
def samples(tmp_path_factory, assets_dir: Path) -> dict[str, Path]:
    return gallery._make_samples(tmp_path_factory.mktemp("samples"))


@pytest.mark.parametrize("entry", gallery.CATALOG, ids=[e.name for e in gallery.CATALOG])
def test_every_entry_is_a_valid_project(entry: gallery.Entry, samples: dict[str, Path], tmp_path: Path):
    project = entry.project
    for key, path in samples.items():
        project = project.replace("{" + key + "}", str(path))
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(project, encoding="utf-8")
    load_and_resolve(yaml_path)


def test_page_from_cached_renders(tmp_path: Path, melt, openshot_transitions, monkeypatch):
    broken = gallery._clip("broken", "speed: 1000", source="video")
    by_name = {e.name: e for e in gallery.CATALOG}
    monkeypatch.setattr(gallery, "CATALOG", (by_name["dissolve"], by_name["fade_in / fade_out"], broken))
    rendered = []
    real_render = gallery._render
    monkeypatch.setattr(gallery, "_render", lambda *a: rendered.append(a[0].name) or real_render(*a))

    page = gallery.build(cache_root=tmp_path / "cache")
    text = page.read_text(encoding="utf-8")
    assert sorted(rendered) == sorted(["dissolve", "fade_in / fade_out", "broken"])
    assert text.count("<video") == 2  # the broken one shows its error instead
    assert "speed" in text and "luma: openshot/ramp_left" in text
    for src in ("files/dissolve-", "files/luma-openshot-ramp-left-"):
        assert src in text
    files = page.parent / "files"
    assert sum(1 for f in files.iterdir() if f.suffix == ".mp4") == 2

    rendered.clear()
    gallery.build(cache_root=tmp_path / "cache")
    assert rendered == []  # all cached, the failure too


def test_cli_gallery_without_opening(tmp_path: Path, melt, openshot_transitions, monkeypatch, capsys):
    # (openshot_transitions: a cache folder of the test's own, and 2 OpenShot maps, not a real install's 412)
    monkeypatch.setattr(gallery, "CATALOG", (gallery.CATALOG[0],))
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail("opened the browser"))
    assert cli.main(["gallery", "--no-open"]) == 0
    assert (tmp_path / "cache" / "video-as-code" / "gallery" / "index.html").exists()


def test_cli_gallery_with_a_missing_file(tmp_path: Path, capsys):
    assert cli.main(["gallery", "--with", str(tmp_path / "nope.jpg"), "--no-open"]) == 1
