"""Lottie animations (.json, .tgs) as bin clips: played by MLT's glaxnimate
producer, as long as their own frames say."""
from __future__ import annotations

import gzip
import json
import os
import subprocess
from pathlib import Path

import pytest

from video_as_code import probe, reverse, xmlgen
from video_as_code.schema import load_and_resolve


def box(path: Path, seconds: float = 2.0) -> Path:
    """A red 80x80 square crossing a 320x240 frame left to right."""
    frames = round(seconds * 25)
    data = {"v": "5.7.4", "fr": 25, "ip": 0, "op": frames, "w": 320, "h": 240, "nm": "box", "ddd": 0, "assets": [],
            "layers": [{"ddd": 0, "ind": 1, "ty": 4, "nm": "box", "sr": 1, "ao": 0, "ip": 0, "op": frames, "st": 0, "bm": 0,
                        "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]},
                               "p": {"a": 1, "k": [{"t": 0, "s": [60, 120, 0], "i": {"x": [0.5], "y": [0.5]}, "o": {"x": [0.5], "y": [0.5]}},
                                                   {"t": frames - 1, "s": [260, 120, 0]}]}},
                        "shapes": [{"ty": "rc", "d": 1, "s": {"a": 0, "k": [80, 80]}, "p": {"a": 0, "k": [0, 0]}, "r": {"a": 0, "k": 0}},
                                   {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}, "r": 1}]}]}
    text = json.dumps(data).encode()
    path.write_bytes(gzip.compress(text) if path.suffix == ".tgs" else text)
    return path


def test_type_and_length(tmp_path: Path):
    assert probe.guess_type(Path("a.json")) == probe.guess_type(Path("a.tgs")) == "animation"
    assert probe.animation_duration_seconds(box(tmp_path / "b.json", 2.0)) == 2.0
    assert probe.animation_duration_seconds(box(tmp_path / "b.tgs", 1.2)) == 1.2


def _project(tmp_path: Path, assets_dir: Path) -> Path:
    box(tmp_path / "box.json")
    path = tmp_path / "project.yaml"
    path.write_text(f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - {{id: bg, path: {assets_dir / "photo2.png"}}}
  - {{id: box, path: box.json}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: bg, start: 0, duration: 2}}
    - type: video
      clips:
        - {{clip: box, start: 0}}
""", encoding="utf-8")
    return path


def test_a_glaxnimate_producer_imported_back(tmp_path: Path, assets_dir: Path):
    resolved = load_and_resolve(_project(tmp_path, assets_dir))
    (clip,) = [c for c in resolved.bin_clips if c.id == "box"]
    assert clip.kind == "animation" and clip.duration_frames == 50
    out = tmp_path / "p.kdenlive"
    xmlgen.write(resolved, out)
    services = [next(p.text for p in e if p.get("name") == "mlt_service") for e in xmlgen.ET.parse(out).getroot()
                if e.tag in ("producer", "chain") and str(tmp_path / "box.json") in "".join(p.text or "" for p in e)]
    assert services and set(services) == {"glaxnimate"}
    data = reverse.import_kdenlive(out).data
    assert any(b.get("path", "").endswith("box.json") for b in data["bin"])


def test_rendered_over_the_track_below(tmp_path: Path, assets_dir: Path, melt):
    out = tmp_path / "p.kdenlive"
    xmlgen.write(load_and_resolve(_project(tmp_path, assets_dir)), out)
    video = tmp_path / "p.mp4"
    candidates = [(melt[0], {**os.environ, **melt[1]})]
    if probe.find_melt():  # (a from-source melt may lack the module the distribution's has)
        candidates.append((probe.find_melt(), probe.melt_env()))
    for exe, env in candidates:
        if "glaxnimate" in subprocess.run([exe, "-query", "producers"], env=env, capture_output=True, text=True).stdout:
            break
    else:
        pytest.skip("no melt here has the glaxnimate module")
    subprocess.run([exe, str(out), "-consumer", f"avformat:{video}"], env=env, capture_output=True, check=True, timeout=120)

    def row(t: float) -> list[tuple[int, int, int]]:
        data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "crop=320:20:0:110,scale=8:1",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return [tuple(data[i:i + 3]) for i in range(0, 24, 3)]

    start, end = row(0.1), row(1.9)
    # (columns 40 px wide, averaged: red over blue where the square is)
    assert start[1][0] > start[1][2] + 40, start  # red on the left, at first
    assert end[6][0] > end[6][2] + 40, end        # on the right, at the end
    assert start[6][2] > 200 and end[1][2] > 200  # the blue still below, elsewhere


def test_a_melt_without_glaxnimate_is_said_so(tmp_path: Path, assets_dir: Path, monkeypatch):
    from video_as_code import meltpass
    from video_as_code.errors import ValidationErrors

    monkeypatch.setattr(meltpass, "is_known", lambda kind, service: not (kind == "producer" and service == "glaxnimate"))
    monkeypatch.setattr(meltpass, "_query", lambda what: {"producers": []} if what == "producers" else None)
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(_project(tmp_path, assets_dir))
    assert "glaxnimate" in str(exc.value)


def test_the_type_said_outright(tmp_path: Path, assets_dir: Path):
    # (a Lottie file whose extension says nothing: the type says it)
    box(tmp_path / "box.json").rename(tmp_path / "box.lottie-json")
    path = _project(tmp_path, assets_dir)
    path.write_text(path.read_text(encoding="utf-8").replace("{id: box, path: box.json}",
                                                             "{id: box, path: box.lottie-json, type: animation}"),
                    encoding="utf-8")
    (clip,) = [c for c in load_and_resolve(path).bin_clips if c.id == "box"]
    assert clip.kind == "animation" and clip.duration_frames == 50
