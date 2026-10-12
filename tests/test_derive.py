"""`derive:` bin clips (models.DeriveSpec, derive.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_yaml

ALBUM = "{album: 'https://photos.app.goo.gl/x'}"


def _project(tmp_path: Path, bin_entries: list[str], sources: str = ALBUM, clips: str = "") -> Path:
    bin_lines = "\n".join(f"  - {entry}" for entry in bin_entries)
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240, sources: {sources}}}
bin:
{bin_lines}
timeline:
  tracks:
    - type: video
      clips: [{clips}]
""",
        encoding="utf-8",
    )
    return path


def _bin(path: Path) -> dict:
    return {c.id: c for c in load_yaml(path).bin}


# --- parsing ----------------------------------------------------------------


def test_derive_output_goes_to_the_derived_cache(tmp_path: Path):
    clips = _bin(_project(tmp_path, ["{id: cut, derive: {source: VID_1.mp4, trim: [1, 2]}}"]))
    spec, path = clips["cut"].derive, clips["cut"].path
    assert (spec.source, spec.trim) == ("VID_1.mp4", (1.0, 2.0))
    assert path.parent == tmp_path / "assets" / ".derived"
    assert path.name.startswith("cut-") and path.suffix == ".mp4"


@pytest.mark.parametrize(
    "derive, suffix",
    [
        ("{path: a.gif, reverse: true}", ".gif"),
        ("{path: a.jpg, crop: {x: 0, y: 0, w: 10, h: 10}}", ".png"),
        ("{path: a.mp3, volume: 2}", ".m4a"),
        ("{path: a.mov, fps: 30}", ".mp4"),
    ],
)
def test_derive_output_kind_follows_the_input(tmp_path: Path, derive: str, suffix: str):
    assert _bin(_project(tmp_path, [f"{{id: d, derive: {derive}}}"]))["d"].path.suffix == suffix


def test_derive_spec_change_changes_the_output_name(tmp_path: Path):
    first = _bin(_project(tmp_path, ["{id: d, derive: {path: a.mp4, trim: [1, 2]}}"]))["d"].path
    same = _bin(_project(tmp_path, ["{id: d, derive: {path: a.mp4, trim: [1, 2]}}"]))["d"].path
    other = _bin(_project(tmp_path, ["{id: d, derive: {path: a.mp4, trim: [1, 3]}}"]))["d"].path
    assert first == same and first != other


def test_derive_from_another_clip_follows_it(tmp_path: Path):
    entries = [
        "{id: loud, derive: {clip: cut, volume: 2}}",  # declared before its input
        "{id: cut, derive: {path: a.mp4, trim: [1, 2]}}",
    ]
    clips = _bin(_project(tmp_path, entries))
    assert clips["loud"].derive.clip == "cut"
    # editing the upstream derive renames (and so regenerates) the downstream one too
    entries[1] = "{id: cut, derive: {path: a.mp4, trim: [1, 3]}}"
    assert _bin(_project(tmp_path, entries))["loud"].path != clips["loud"].path


@pytest.mark.parametrize(
    "entries, needle",
    [
        (["{id: d, path: a.mp4, derive: {path: a.mp4, fps: 30}}"], "derive"),
        (["{id: d, derive: [a.mp4]}"], "derive"),
        (["{id: d, derive: {path: a.mp4, fps: 30, speed: 2}}"], "speed"),
        (["{id: d, derive: {fps: 30}}"], "exactly one input"),
        (["{id: d, derive: {path: a.mp4, clip: x, fps: 30}}"], "exactly one input"),
        (["{id: d, derive: {path: a.mp4, trim: [3, 2]}}"], "derive.trim"),
        (["{id: d, derive: {path: a.mp4, trim: 3}}"], "derive.trim"),
        (["{id: d, derive: {path: a.mp4, crop: {x: 0, y: 0, w: 0, h: 10}}}"], "derive.crop"),
        (["{id: d, derive: {path: a.mp4, scale: [-1, -1]}}"], "derive.scale"),
        (["{id: d, derive: {path: a.mp4, fps: 0}}"], "derive.fps"),
        (["{id: d, derive: {path: a.mp4, reverse: maybe}}"], "derive.reverse"),
        (["{id: d, derive: {path: a.mp4, volume: -1}}"], "derive.volume"),
        (["{id: d, derive: {path: a.mp4}}"], "no operation"),
        (["{id: d, derive: {clip: nope, fps: 30}}"], "nope"),
        (["{id: t, type: title, text: Hi}", "{id: d, derive: {clip: t, fps: 30}}"], "title"),
        (["{id: a, derive: {clip: b, fps: 30}}", "{id: b, derive: {clip: a, fps: 30}}"], "cycle"),
        (["{id: d, derive: {source: ../x.mp4, fps: 30}}"], "../x.mp4"),
    ],
)
def test_derive_invalid(tmp_path: Path, entries: list[str], needle: str):
    with pytest.raises(ValidationErrors) as exc:
        load_yaml(_project(tmp_path, entries))
    assert needle in str(exc.value)


def test_derive_source_needs_an_album(tmp_path: Path):
    with pytest.raises(ValidationErrors) as exc:
        load_yaml(_project(tmp_path, ["{id: d, derive: {source: VID_1.mp4, fps: 30}}"], sources="{}"))
    assert "sources.album" in str(exc.value)
