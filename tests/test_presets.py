"""presets.py: `presets:`, `presets_file:` and `use:` expanded before the
project file is read."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_as_code import presets
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve


def _expand(data: dict, tmp_path: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    return presets.expand(data, tmp_path / "project.yaml", errors), errors


def test_use_merges_the_written_keys_last(tmp_path: Path):
    data, errors = _expand({
        "presets": {"page": {"transition": "gl", "gl": "BookFlip", "transition_duration": 0.8}},
        "scenes": [{"use": "page", "transition_duration": 1.5, "clips": ["a.jpg"]}],
    }, tmp_path)
    assert errors == [] and "presets" not in data
    assert data["scenes"] == [{"transition": "gl", "gl": "BookFlip", "transition_duration": 1.5, "clips": ["a.jpg"]}]


def test_a_list_in_order_and_presets_using_presets(tmp_path: Path):
    data, errors = _expand({
        "presets": {
            "a": {"x": 1, "y": 1}, "b": {"y": 2, "z": 2},
            "ab": {"use": ["a", "b"], "w": 0},
        },
        "bin": [{"id": "c", "use": "ab"}],
    }, tmp_path)
    assert errors == [] and data["bin"] == [{"x": 1, "y": 2, "z": 2, "w": 0, "id": "c"}]


def test_nested_maps(tmp_path: Path):
    data, errors = _expand({
        "presets": {"warm": {"lut": "w.cube"}},
        "timeline": {"tracks": [{"type": "video", "clips": [{"clip": "a", "start": 0, "use": "warm"}]}]},
    }, tmp_path)
    assert errors == [] and data["timeline"]["tracks"][0]["clips"][0]["lut"] == "w.cube"


def test_a_value_replaces_the_presets_whole(tmp_path: Path):
    data, _ = _expand({
        "presets": {"g": {"film_grain": {"noise": 12, "contrast": 5}}},
        "bin": [{"id": "a", "use": "g", "film_grain": {"noise": 30}}],
    }, tmp_path)
    assert data["bin"][0]["film_grain"] == {"noise": 30}


def test_presets_file_then_the_projects_own(tmp_path: Path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "presets.yaml").write_text("presets:\n  page: {gl: cube}\n  card: {font: Sans}\n", encoding="utf-8")
    data, errors = _expand({
        "presets_file": "shared/presets.yaml",
        "presets": {"page": {"gl": "doorway"}},
        "bin": [{"id": "a", "use": ["page", "card"]}],
    }, tmp_path)
    assert errors == [] and data["bin"][0] == {"gl": "doorway", "font": "Sans", "id": "a"}
    assert "presets_file" not in data


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"presets": {"a": {"x": 1}}, "bin": [{"use": "b"}]}, "bin[0]: use: 'b' isn't a preset (there are: a)"),
        ({"presets": {"a": {"use": "b"}, "b": {"use": "a"}}, "bin": [{"use": "a"}]}, "a -> b -> a"),
        ({"presets": {"a": "x"}}, "presets.a"),
        ({"presets": {"a": {"x": 1}}, "bin": [{"use": 3}]}, "bin[0].use=3"),
        ({"presets_file": "nowhere.yaml"}, "nowhere.yaml"),
    ],
)
def test_errors_say_where(tmp_path: Path, data: dict, expected: str):
    _, errors = _expand(data, tmp_path)
    assert any(expected in e for e in errors), errors


def test_a_whole_project(tmp_path: Path, assets_dir: Path):
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
presets:
  slow-fade: {{transition: dissolve, transition_duration: 1.5}}
  card: {{type: title, font_size: 30, font_weight: bold}}
bin:
  - {{id: t, text: "Hi", use: card}}
scenes:
  - use: slow-fade
    clips: [{assets_dir / "photo.png"}, {assets_dir / "photo2.png"}, {{clip: t}}]
    duration: 6
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(path)
    (title,) = [c for c in resolved.bin_clips if c.id == "t"]
    assert 'font-weight="700"' in Path(title.path).read_text(encoding="utf-8")  # (the preset's keys reached it)


def test_unknown_preset_stops_loading(tmp_path: Path):
    path = tmp_path / "project.yaml"
    path.write_text("project: {id: p1, title: T, fps: 25, width: 320, height: 240}\nscenes:\n  - use: nope\n    clips: []\n", encoding="utf-8")
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(path)
    assert "nope" in str(exc.value)
