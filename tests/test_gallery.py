"""gallery.py: every catalog entry is a valid project, and the page is
built from cached renders - against stand-ins (conftest.py's
openshot_transitions, a small catalog) so it stays quick."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_as_code import cli, gallery, i18n
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
    monkeypatch.setattr(gallery, "gl_entries", lambda: ())
    monkeypatch.setattr(gallery, "lut_entries", lambda: ())
    monkeypatch.setattr(gallery, "glsl_entries", lambda: ())  # (whether or not this machine built the plugin)
    monkeypatch.setattr(gallery, "openshot_entries", lambda: ())  # (whether or not libopenshot is here)
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
    monkeypatch.setattr(gallery, "lut_entries", lambda: ())
    monkeypatch.setattr(gallery, "glsl_entries", lambda: ())
    monkeypatch.setattr(gallery, "openshot_entries", lambda: ())
    monkeypatch.setattr(gallery, "melt_entries", lambda cache=None: ())
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail("opened the browser"))
    assert cli.main(["gallery", "--no-open"]) == 0
    assert (tmp_path / "cache" / "video-as-code" / "gallery" / "index.html").exists()


def test_cli_gallery_with_a_missing_file(tmp_path: Path, capsys):
    assert cli.main(["gallery", "--with", str(tmp_path / "nope.jpg"), "--no-open"]) == 1


def test_links_to_every_section_folded():
    page = gallery._page([], {}, [], [Path(__file__), Path(__file__)], Path(__file__).parent)
    for section in gallery.SECTIONS:
        assert f'<a href="#{section}" data-tab=' in page and f'<section id="{section}" data-section="{section}" data-tab=' in page
    assert "<details open" not in page
    assert set(gallery.SECTIONS) == {s for sections in gallery.TABS.values() for s in sections}  # each in a tab


def test_own_entries_described():
    by_name = {e.name: e for e in gallery.CATALOG}
    entry, out = by_name["chroma_key"], Path(__file__).parent / "files" / "x.mp4"
    page = gallery._page([(entry, out)], {}, [], [Path(__file__), Path(__file__)], Path(__file__).parent)
    assert "Makes a colour transparent" in page
    for e in gallery.CATALOG:  # every one of them has its line
        assert f"gallery.about.{gallery._slug(e.name)}" in gallery.i18n.CATALOGS["es"], e.name


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
        frei0r_gltransition=("transition", ["Video"]), avfilter_datascope=("filter", ["Video"]),
        avfilter_scale_cuda=("filter", ["Video"]), movit_blur=("filter", ["Video"]),
    ))
    by_name = {e.name: e for e in gallery.melt_entries()}
    assert "frei0r.gltransition" not in by_name  # (its own section)
    assert by_name["avfilter.negate"].section == "melt_filters"
    assert by_name["avfilter.negate"].snippet == "melt:\n  - avfilter.negate"
    assert by_name["avfilter.aecho"].section == "melt_audio" and by_name["avfilter.aecho"].render
    assert by_name["luma.x"].snippet == "transition: melt/luma.x"
    assert by_name["mix.x"].section == "melt_audio" and not by_name["mix.x"].render
    assert by_name["avfilter.datascope"].section == "melt_analysis"  # (measures; doesn't change the picture)
    assert by_name["avfilter.negate"].source == "video"  # (melt's filters play over the video sample)
    for name in ("avfilter.scale_cuda", "movit.blur"):  # can't work in a plain melt render: listed, greyed
        assert by_name[name].unavailable and not by_name[name].render


def test_melt_section_tells_what_changes_nothing(tmp_path: Path, melt, monkeypatch):
    """At their defaults, brightness and avfilter.hue change nothing,
    avfilter.negate and avfilter.aecho do: rendered and told apart; then
    brightness is shown with another level (melt gives its range), and
    hue, whose properties have none, is marked for setting them."""
    monkeypatch.setattr(gallery, "CATALOG", ())
    monkeypatch.setattr(gallery, "gl_entries", lambda: ())
    monkeypatch.setattr(gallery, "lut_entries", lambda: ())
    monkeypatch.setattr(gallery, "glsl_entries", lambda: ())
    monkeypatch.setattr(gallery, "openshot_entries", lambda: ())
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
    assert "unchanged" in cards["avfilter.hue"] and "need setting" in cards["avfilter.hue"]
    assert "unchanged" not in cards["brightness"] and "shown with" in cards["brightness"]
    assert "brightness: {" in cards["brightness"]  # (its snippet, with the value)
    assert "unchanged" not in cards["avfilter.negate"] and "shown with" not in cards["avfilter.negate"]
    assert "<audio" in cards["avfilter.aecho"] and "unchanged" not in cards["avfilter.aecho"]


def test_tweaks_from_ranges():
    entry = gallery.Entry("melt_filters", "x", "", "", params=(
        (("default", 1.0), ("maximum", 15.0), ("minimum", 0.0), ("name", "level"), ("type", "float")),
        (("default", 200), ("maximum", 255), ("minimum", 0), ("name", "u"), ("type", "integer")),
        (("default", 0), ("name", "flag"), ("type", "boolean")),
        (("default", "'1'"), ("name", "av.s"), ("type", "string")),
        (("default", 1.0), ("maximum", 15.0), ("minimum", 0.0), ("name", "end (*DEPRECATED*)"), ("type", "float")),
    ))
    # the far end of each range first, then the near ends
    assert gallery._tweaks(entry) == [{"level": 11.25}, {"u": 64}, {"flag": True}, {"level": 3.75}, {"u": 191}]
    assert gallery._tweaks(entry, tries=2) == [{"level": 11.25}, {"u": 64}]


def test_failures_told_in_a_line():
    assert "crashed" in gallery._failure("melt: filter.c:12: f: Assertion `x' failed.")
    assert "ffmpeg" in gallery._failure("[Parsed_x] Cannot configure the filter graph")
    assert gallery._failure("something else") == i18n.msg("gallery.failed")


def test_audio_analysers_listed_without_a_preview():
    for name in ("avfilter.astats", "audiolevel", "sox.stats", "avfilter.ebur128"):
        assert gallery._AUDIO_ANALYSIS.match(name), name
    assert not gallery._AUDIO_ANALYSIS.match("avfilter.aecho")


def test_clip_keys_that_change_over_time_apart():
    sections = {e.name: e.section for e in gallery.CATALOG}
    assert sections["color_grade_keyframes"] == sections["speed"] == sections["fade_in / fade_out"] == "clip_time"
    assert sections["position / size"] == sections["color_grade"] == sections["lut"] == "clip"


def test_a_card_per_users_lut(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    folder = tmp_path / "video-as-code" / "luts"
    folder.mkdir(parents=True)
    (folder / "teal.cube").write_text("LUT_3D_SIZE 2\n" + "0 0 0\n" * 8, encoding="utf-8")
    (entry,) = gallery.lut_entries()
    assert entry.snippet == "lut: teal" and entry.section == "clip" and "teal.cube" in entry.description


def test_a_card_per_bundled_glsl_shader(monkeypatch):
    monkeypatch.setattr(gallery.glfilters, "installed", lambda: True)
    entries = {e.name: e for e in gallery.glsl_entries()}
    assert entries["glsl: crt"].section == "clip" and entries["glsl: wave"].section == "clip_time"
    assert entries["glsl: vhs"].snippet == "glsl: vhs" and entries["glsl: vhs"].source == "video"


def test_openshot_effects_a_card_each(monkeypatch):
    """Over the video sample, a sound one over the sample sound, the
    libopenshot version in the project (a new one renders them anew)."""
    from video_as_code import openshot_effects
    monkeypatch.setattr(openshot_effects, "catalog", lambda: {
        "Wave": {"props": {}, "description": "Waves.", "audio": False},
        "ChromaKey": {"props": {}, "description": "", "audio": False},
        "Echo": {"props": {}, "description": "", "audio": True},
        "Caption": {"props": {}, "description": "", "audio": False},
    })
    monkeypatch.setattr(openshot_effects, "version", lambda: "9.9")
    cards = {e.name: e for e in gallery.openshot_entries()}
    assert {e.section for e in cards.values()} == {"openshot"}
    assert cards["Wave"].source == "video" and "trim: [0, 3]" in cards["Wave"].project
    assert cards["ChromaKey"].source == "green" and 'color: "#00ff00"' in cards["ChromaKey"].snippet
    assert cards["Echo"].source == "sound" and "type: audio" in cards["Echo"].project and gallery._audible(cards["Echo"])
    assert not cards["Caption"].render and "type: title" in cards["Caption"].unavailable  # (grey, saying why)
    assert "libopenshot: 9.9" in cards["Wave"].project and cards["Wave"].description == "Waves."


def test_public_samples_without_the_photos():
    recipes = " ".join(" ".join(args) for args in gallery._recipes(photos=False).values())
    assert "/usr/share/backgrounds" not in recipes and "mandelbrot" in recipes


def test_a_render_that_hangs_is_killed_with_what_it_started(tmp_path: Path):
    """A timed-out render takes its melt with it (it once didn't: a melt
    that hung went on for hours)."""
    import subprocess
    import time

    pid_file = tmp_path / "child.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        gallery._run(["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"], tmp_path, 5)  # (time to start, even on a busy machine)
    child = int(pid_file.read_text())

    def alive() -> bool:
        try:
            return Path(f"/proc/{child}/stat").read_text().split()[2] != "Z"  # (a zombie is dead)
        except (FileNotFoundError, ProcessLookupError):
            return False

    deadline = time.monotonic() + 3  # (a SIGKILL lands soon, not at once)
    while alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive()


# --- part of the docs (docs.py): each card linked to its key ------------------

def test_a_card_knows_its_key():
    known = {"transition", "luma", "openshot", "derive", "melt", "gl"}
    card = lambda snippet: gallery.Entry("x", "x", snippet, "")  # noqa: E731
    assert gallery.doc_key(card("transition: wipe\nluma: heart"), known) == "luma"
    assert gallery.doc_key(card("derive:\n  openshot:\n    - {effect: Wave}"), known) == "openshot"
    assert gallery.doc_key(card("melt:\n  - brightness: {level: 2}"), known) == "melt"
    assert gallery.doc_key(card("transition: gl\ngl: cube\ngl_params: {persp: 1}"), known) == "gl"
    assert gallery.doc_key(card("transition: dissolve"), known) == "transition"
    known |= {"type", "text", "shadow", "overlay_text", "overlay_font_size"}
    assert gallery.doc_key(card("type: title\ntext: Hello\nshadow: true"), known) == "text"
    assert gallery.doc_key(card("overlay_text: Hi\noverlay_font_size: 40"), known) == "overlay_text"
    assert gallery.doc_key(card("speed: 2"), known) is None


def test_the_gallery_links_each_card_to_its_key(tmp_path: Path):
    entry = gallery.Entry("clip", "fade", "fade_in: 1", "", render=False)
    pictures = [tmp_path / "a.png", tmp_path / "b.png"]
    for path in pictures:
        path.write_bytes(b"png")
    page = gallery._page([(entry, None)], {}, [], pictures, tmp_path, {}, {"fade_in": "../docs/project-yaml.html#key-fade_in"},
                         "../docs/index.html")
    assert '<figure id="card-clip-fade"' in page
    assert 'class="to-doc" href="../docs/project-yaml.html#key-fade_in"' in page
    assert 'class="docs" href="../docs/index.html"' in page


def test_inside_the_docs_the_gallery_wears_their_bar(tmp_path: Path):
    entry = gallery.Entry("clip", "fade", "fade_in: 1", "", render=False)
    pictures = [tmp_path / "a.png", tmp_path / "b.png"]
    for path in pictures:
        path.write_bytes(b"png")
    bar = '<nav class="site"><a href="../docs/index.html">video-as-code</a></nav>'
    page = gallery._page([(entry, None)], {}, [], pictures, tmp_path, {}, {}, "../docs/index.html", "", bar)
    assert bar in page and 'class="hide-unchanged in-site"' in page
    assert 'class="docs"' not in page  # (the bar links the docs already)
    assert "@view-transition" in page and '<nav class="sections">' in page
    themed = gallery._page([(entry, None)], {}, [], pictures, tmp_path, {}, {}, "../docs/index.html", "", bar, ":root{--bg:sepia}")
    assert themed.index("<style>:root{--bg:sepia}</style>") > themed.index("@view-transition")  # (over theirs)
