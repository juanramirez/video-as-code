"""`lut:` (a 3D LUT file on a clip, avfilter.lut3d): resolved against the
project file, emitted as kdenlive's own "Apply LUT", brought back by
import."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_as_code import reverse, xmlgen
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve


def write_cube(path: Path, invert: bool = False, size: int = 5) -> Path:
    lines = [f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rgb = [c / (size - 1) for c in (r, g, b)]
                lines.append(" ".join(f"{1 - c if invert else c:.6f}" for c in rgb))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _project(tmp_path: Path, assets_dir: Path, clip_keys: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: red
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: red, start: 0, duration: 1, {clip_keys}}}
""",
        encoding="utf-8",
    )
    return path


def test_relative_to_the_project_file(tmp_path: Path, assets_dir: Path):
    (tmp_path / "looks").mkdir()
    cube = write_cube(tmp_path / "looks" / "warm.cube")
    resolved = load_and_resolve(_project(tmp_path, assets_dir, "lut: looks/warm.cube, lut_interp: trilinear"))
    (placement,) = resolved.tracks[0].placements
    assert placement.lut == str(cube.resolve()) and placement.lut_interp == "trilinear"


@pytest.mark.parametrize(
    "keys, expected",
    [
        ("lut: nowhere.cube", "nowhere.cube"),
        ("lut: look.png", ".cube"),
        ("lut: look.cube, lut_interp: cubic", "tetrahedral"),
    ],
)
def test_rejected(tmp_path: Path, assets_dir: Path, keys: str, expected: str):
    write_cube(tmp_path / "look.cube")
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(_project(tmp_path, assets_dir, keys))
    assert expected in str(exc.value)


def test_emitted_and_imported(tmp_path: Path, assets_dir: Path):
    cube = write_cube(tmp_path / "look.cube")
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(load_and_resolve(_project(tmp_path, assets_dir, "lut: look.cube, lut_interp: nearest")), kdenlive_path)

    root = xmlgen.ET.parse(kdenlive_path).getroot()
    (f,) = [f for f in root.iter("filter") if any(p.text == "avfilter.lut3d" for p in f.findall("property"))]
    props = {p.get("name"): p.text for p in f.findall("property")}
    assert props == {
        "mlt_service": "avfilter.lut3d", "kdenlive_id": "avfilter.lut3d",
        "av.file": str(cube.resolve()), "av.interp": "nearest",
    }
    clip = reverse.import_kdenlive(kdenlive_path).data["timeline"]["tracks"][0]["clips"][0]
    assert clip["lut"] == str(cube.resolve()) and clip["lut_interp"] == "nearest"


def test_on_a_scene_clip(tmp_path: Path, assets_dir: Path):
    write_cube(tmp_path / "look.cube")
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - clips: [{{path: {assets_dir / "photo.png"}, lut: look.cube}}, {assets_dir / "photo2.png"}]
    duration: 2
""",
        encoding="utf-8",
    )
    placements = [p for t in load_and_resolve(path).tracks for p in t.placements]
    assert [p.lut is not None for p in placements] == [True, False]


def test_by_name_from_the_projects_luts_then_the_users(tmp_path: Path, assets_dir: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    users = tmp_path / "data" / "video-as-code" / "luts"
    users.mkdir(parents=True)
    write_cube(users / "warm.cube")
    write_cube(users / "cold.cube")
    (tmp_path / "luts").mkdir()
    write_cube(tmp_path / "luts" / "warm.cube", invert=True)  # (the project's own wins)
    resolved = load_and_resolve(_project(tmp_path, assets_dir, "lut: warm"))
    assert resolved.tracks[0].placements[0].lut == str((tmp_path / "luts" / "warm.cube").resolve())
    resolved = load_and_resolve(_project(tmp_path, assets_dir, "lut: cold"))
    assert resolved.tracks[0].placements[0].lut == str((users / "cold.cube").resolve())
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(_project(tmp_path, assets_dir, "lut: hot"))
    assert "there are: cold, warm" in str(exc.value)
