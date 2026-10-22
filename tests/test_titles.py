"""Titles' typography (titles.py, schema.py): weight, italic, outline and
shadow written as kdenlive and MLT's kdenlivetitle read them, checked,
and read back by import."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from video_as_code import titles
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve


def _content(**style) -> ET.Element:
    xml = titles.render_title_xml("Hi", width=320, height=180, duration_frames=25, **style)
    return ET.fromstring(xml).find("item/content")


def test_defaults_as_qt6_writes_them():
    content = _content()
    assert content.get("font-weight") == "400" and content.get("font-italic") == "0"
    assert content.get("font-outline") == "0" and content.get("shadow") is None


def test_style_written_and_read_back():
    xml = titles.render_title_xml(
        "Hi", width=320, height=180, duration_frames=25, font_weight=700, italic=True,
        outline=2.5, outline_color=(255, 0, 0, 255), shadow=((0, 0, 0, 160), 6, 3, 4),
    )
    content = ET.fromstring(xml).find("item/content")
    assert content.get("font-weight") == "700" and content.get("font-italic") == "1"
    assert content.get("font-outline") == "2.5" and content.get("font-outline-color") == "255,0,0,255"
    assert content.get("shadow") == "1;#a0000000;6;3;4"  # (kdenlive's own: #AARRGGBB)
    back = titles.parse_title_xml(xml)
    assert back["font_weight"] == 700 and back["italic"] is True
    assert back["outline"] == 2.5 and back["outline_color"] == [255, 0, 0, 255]
    assert back["shadow"] == {"color": [0, 0, 0, 160], "blur": 6, "offset": [3, 4]}


@pytest.mark.parametrize("old, new", [("50", None), ("75", 700)])
def test_qt5_weights_read_on_qt6s_scale(old, new):
    xml = titles.render_title_xml("Hi", width=320, height=180, duration_frames=25).replace('font-weight="400"', f'font-weight="{old}"')
    assert titles.parse_title_xml(xml).get("font_weight") == new


def _project(tmp_path: Path, title_keys: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - {{id: card, type: title, text: "Hello", {title_keys}}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: card, start: 0, duration: 1}}
""",
        encoding="utf-8",
    )
    return path


def test_names_for_weights(tmp_path: Path):
    resolved = load_and_resolve(_project(tmp_path, "font_weight: bold, shadow: true"))
    (clip,) = [c for c in resolved.bin_clips if c.id == "card"]
    xml = Path(clip.path).read_text(encoding="utf-8")
    assert 'font-weight="700"' in xml and 'shadow="1;#a0000000;6;3;3"' in xml


@pytest.mark.parametrize(
    "keys, expected",
    [
        ("font_weight: heavy", "font_weight"),
        ("font_weight: 1000", "font_weight"),
        ("italic: yes please", "italic"),
        ("outline: -1", "outline"),
        ("outline: 2, outline_color: [255, 0, 0]", "outline_color"),
        ("shadow: {colour: [0, 0, 0, 255]}", "shadow"),
        ("shadow: {offset: [1.5, 2]}", "shadow"),
    ],
)
def test_rejected(tmp_path: Path, keys: str, expected: str):
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(_project(tmp_path, keys))
    assert expected in str(exc.value)
