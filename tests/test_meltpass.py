"""`melt:` filters and `melt/<service>` transitions (meltpass.py): checked
against the installed melt, emitted as given, brought back by import."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from video_as_code import meltpass, probe, reverse, xmlgen
from video_as_code.errors import ValidationErrors
from video_as_code.models import MeltService
from video_as_code.schema import load_and_resolve

needs_melt = pytest.mark.skipif(probe.find_melt() is None, reason="needs melt to check services against")


@pytest.fixture
def no_melt(monkeypatch):
    monkeypatch.setattr(probe, "find_melt", lambda: None)
    meltpass._query.cache_clear()
    yield
    meltpass._query.cache_clear()


def _parse(raw) -> tuple[tuple[MeltService, ...], list[str]]:
    errors: list[str] = []
    return meltpass.parse_filters(raw, "c", errors), errors


@needs_melt
def test_filters_parsed_with_their_values_as_written():
    filters, errors = _parse([{"avfilter.hue": {"av.h": "0=0;100=180", "av.s": 1.5}}, "frei0r.invert0r"])
    assert errors == []
    assert filters == (
        MeltService("avfilter.hue", (("av.h", "0=0;100=180"), ("av.s", "1.5"))),
        MeltService("frei0r.invert0r"),
    )


@needs_melt
def test_frei0r_parameter_by_name_or_number():
    for name in ("diffspace", "1"):
        _, errors = _parse([{"frei0r.cartoon": {name: 0.4}}])
        assert errors == [], name


@needs_melt
@pytest.mark.parametrize(
    "raw, expected",
    [
        ([{"frei0r.cartooon": None}], "frei0r.cartoon"),  # unknown service: the closest named
        ([{"avfilter.hue": {"av.hh": 1}}], "av.h"),  # unknown property: the same
    ],
)
def test_unknown_names_rejected_with_the_closest(raw, expected):
    _, errors = _parse(raw)
    assert len(errors) == 1 and expected in errors[0]


@pytest.mark.parametrize("raw", ["frei0r.cartoon", [{"a": 1, "b": 2}], [{"avfilter.hue": {"av.h": [1, 2]}}], [3]])
def test_malformed_rejected(raw, no_melt):
    _, errors = _parse(raw)
    assert errors


def test_nothing_checked_without_melt(no_melt):
    filters, errors = _parse([{"no.such.filter": {"anything": True}}])
    assert errors == [] and filters == (MeltService("no.such.filter", (("anything", "1"),)),)


def _project(tmp_path: Path, assets_dir: Path, clip_melt: str, transitions: str = "") -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: red
    path: {assets_dir / "photo.png"}
  - id: blue
    path: {assets_dir / "photo2.png"}
timeline:
  tracks:
    - type: video
      name: A
      clips:
        - {{clip: red, start: 0, duration: 2.5, melt: {clip_melt}}}
    - type: video
      name: B
      clips:
        - {{clip: blue, start: 1.5, duration: 2.5}}
{transitions}
""",
        encoding="utf-8",
    )
    return path


@needs_melt
def test_emitted_after_the_clips_own_filters(tmp_path: Path, assets_dir: Path):
    path = _project(
        tmp_path,
        assets_dir,
        '[{avfilter.hue: {av.h: 90}}]',
        "transitions:\n  - {type: melt/luma, track_a: A, track_b: B, start: 1.5, end: 2.5, properties: {softness: 0.2}}",
    )
    root = xmlgen.generate(load_and_resolve(path)).getroot()
    entry = next(e for e in root.iter("entry") if e.findall("filter"))
    services = [{p.get("name"): p.text for p in f.findall("property")}.get("mlt_service") for f in entry.findall("filter")]
    assert services[-1] == "avfilter.hue"
    hue = {p.get("name"): p.text for p in entry.findall("filter")[-1].findall("property")}
    assert hue == {"mlt_service": "avfilter.hue", "kdenlive_id": "avfilter.hue", "av.h": "90"}
    lumas = [
        t for t in root.iter("transition")
        if any(p.get("name") == "mlt_service" and p.text == "luma" for p in t.findall("property"))
    ]
    (luma,) = lumas
    props = {p.get("name"): p.text for p in luma.findall("property")}
    assert props["softness"] == "0.2" and luma.get("in") == "38" and luma.get("out") == "62"


@needs_melt
def test_unknown_transition_rejected(tmp_path: Path, assets_dir: Path):
    path = _project(
        tmp_path, assets_dir, "[]", "transitions:\n  - {type: melt/lumaa, track_a: A, track_b: B, start: 1.5, end: 2.5}"
    )
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "luma" in str(exc.value)


@needs_melt
def test_scene_transition_and_clip_filters(tmp_path: Path, assets_dir: Path):
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - transition: melt/luma
    transition_properties: {{softness: 0.5}}
    clips:
      - {{path: {assets_dir / "photo.png"}, melt: [frei0r.invert0r]}}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(path)
    (t,) = resolved.melt_transitions
    assert t.melt == MeltService("luma", (("softness", "0.5"),))
    placements = [p for track in resolved.tracks for p in track.placements]
    assert [p.melt_filters for p in placements if p.melt_filters] == [(MeltService("frei0r.invert0r"),)]


@needs_melt
def test_import_brings_them_back(tmp_path: Path, assets_dir: Path):
    path = _project(
        tmp_path,
        assets_dir,
        '[{avfilter.hue: {av.h: 90}}, frei0r.invert0r]',
        "transitions:\n  - {type: melt/luma, track_a: A, track_b: B, start: 1.5, end: 2.5, properties: {softness: 0.2}}",
    )
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(load_and_resolve(path), kdenlive_path)

    data = reverse.import_kdenlive(kdenlive_path).data
    clip = data["timeline"]["tracks"][0]["clips"][0]
    assert clip["melt"] == [{"avfilter.hue": {"av.h": "90"}}, {"frei0r.invert0r": None}]
    (t,) = [t for t in data["transitions"] if t["type"].startswith("melt/")]
    assert t["type"] == "melt/luma" and t["properties"]["softness"] == "0.2"


@needs_melt
def test_import_keeps_a_filter_melt_lacks_raw(tmp_path: Path, assets_dir: Path):
    path = _project(tmp_path, assets_dir, "[]")
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(load_and_resolve(path), kdenlive_path)
    tree = ET.parse(kdenlive_path)
    entry = next(e for e in tree.getroot().iter("entry") if e.get("producer"))
    f = ET.SubElement(entry, "filter", {"id": "x"})
    ET.SubElement(f, "property", {"name": "mlt_service"}).text = "some.newer.filter"
    tree.write(kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)
    assert "melt" not in result.data["timeline"]["tracks"][0]["clips"][0]
    assert any("some.newer.filter" in r.xml for r in result.raw_items)
