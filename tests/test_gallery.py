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
    monkeypatch.setattr(gallery, "gl_entries", lambda: ())  # (whether or not this machine built the plugin)
    monkeypatch.setattr(gallery, "melt_entries", lambda cache=None: ())
    rendered = []
    real_render = gallery._render
    monkeypatch.setattr(gallery, "_render", lambda *a: rendered.append(a[0].name) or real_render(*a))

    page = gallery.build(cache_root=tmp_path / "cache")
    text = page.read_text(encoding="utf-8")
    assert sorted(rendered) == sorted(["dissolve", "fade_in / fade_out", "broken"])
    assert text.count("<video") == 2  # the broken one shows its error instead
    assert "speed" in text and "transition: wipe\nluma: openshot/ramp_left" in text
    assert "files/transitions-dissolve-" in text
    # what the page reads the pixels of is in it, not in files beside it
    # (a page opened from a file can't read another file's pixels)
    for src in ('data-src="data:image/jpeg;base64,', 'data-from="data:image/jpeg;base64,', 'data-to="data:image/jpeg;base64,'):
        assert src in text
    files = page.parent / "files"
    assert sum(1 for f in files.iterdir() if f.suffix == ".mp4") == 2

    rendered.clear()
    gallery.build(cache_root=tmp_path / "cache")
    assert rendered == []  # all cached, the failure too


def test_cli_gallery_without_opening(tmp_path: Path, melt, openshot_transitions, monkeypatch, capsys):
    # (openshot_transitions: a cache folder of the test's own, and 2 OpenShot maps, not a real install's 412)
    monkeypatch.setattr(gallery, "CATALOG", (gallery.CATALOG[0],))
    monkeypatch.setattr(gallery, "gl_entries", lambda: ())
    monkeypatch.setattr(gallery, "melt_entries", lambda cache=None: ())
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail("opened the browser"))
    assert cli.main(["gallery", "--no-open"]) == 0
    assert (tmp_path / "cache" / "video-as-code" / "gallery" / "index.html").exists()


def test_cli_gallery_with_a_missing_file(tmp_path: Path, capsys):
    assert cli.main(["gallery", "--with", str(tmp_path / "nope.jpg"), "--no-open"]) == 1


def test_links_to_every_section():
    page = gallery._page([], {}, [], [Path(__file__), Path(__file__)], Path(__file__).parent)
    for section in gallery.SECTIONS:
        assert f'<a href="#{section}">' in page and f'<section id="{section}"' in page


def _catalog(**services) -> dict:
    """A stand-in for melt's catalog: name -> tags."""
    out = {"transition": {}, "filter": {}}
    for name, (kind, tags) in services.items():
        out[kind][name.replace("_", ".", 1)] = {"title": name, "description": f"what {name} does", "tags": tags}
    return out


def test_melt_entries_by_kind(monkeypatch):
    monkeypatch.setattr(gallery, "_melt_catalog", lambda cache=None: _catalog(
        avfilter_negate=("filter", ["Video"]), avfilter_aecho=("filter", ["Audio"]),
        luma_x=("transition", ["Video"]), mix_x=("transition", ["Audio"]),
        frei0r_gltransition=("transition", ["Video"]),
    ))
    by_name = {e.name: e for e in gallery.melt_entries()}
    assert "frei0r.gltransition" not in by_name  # (its own section)
    assert by_name["avfilter.negate"].section == "melt_filters"
    assert by_name["avfilter.negate"].snippet == "melt:\n  - avfilter.negate"
    assert by_name["avfilter.aecho"].section == "melt_audio" and by_name["avfilter.aecho"].render
    assert by_name["luma.x"].snippet == "transition: melt/luma.x"
    assert by_name["mix.x"].section == "melt_audio" and not by_name["mix.x"].render


def test_melt_section_tells_what_changes_nothing(tmp_path: Path, melt, monkeypatch):
    """At their defaults, brightness and avfilter.hue change nothing,
    avfilter.negate and avfilter.aecho do: rendered, and told apart."""
    monkeypatch.setattr(gallery, "CATALOG", ())
    monkeypatch.setattr(gallery, "gl_entries", lambda: ())
    real = {name: (kind, gallery._describe_service(kind, name)) for kind, name in (
        ("filter", "brightness"), ("filter", "avfilter.hue"), ("filter", "avfilter.negate"), ("filter", "avfilter.aecho"),
    )}
    catalog = {"transition": {}, "filter": {name: meta for name, (_, meta) in real.items()}}
    monkeypatch.setattr(gallery, "_melt_catalog", lambda cache=None: catalog)
    page = gallery.build(cache_root=tmp_path / "cache").read_text(encoding="utf-8")
    cards = {
        name: card.split("</figure>")[0] for card in page.split("<figure")[1:]
        for name in (*real, "original")
        if f"<figcaption>{name}<" in card
    }
    assert set(cards) == {*real, "original"}
    assert "unchanged" in cards["avfilter.hue"] and "unchanged" in cards["brightness"]
    assert "unchanged" not in cards["avfilter.negate"]
    assert "<audio" in cards["avfilter.aecho"] and "unchanged" not in cards["avfilter.aecho"]

