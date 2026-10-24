"""build --format openshot: an OpenShot project (osp.py, and
openshot_render.py's --project), checked by playing it with libopenshot's
own Timeline - what OpenShot plays and exports with."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_as_code import osp
from video_as_code.cli import main
from video_as_code.schema import load_and_resolve

from test_openshot_effects import needs_openshot


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text("project: {id: p1, title: T, fps: 25, width: 320, height: 240}\n" + body, encoding="utf-8")
    return path


def _fades(tmp_path: Path, assets_dir: Path) -> Path:
    return _write(tmp_path, f"""
bin:
  - {{id: red, path: {assets_dir / "photo.png"}}}
  - {{id: tone, path: {assets_dir / "tone.mp3"}}}
  - {{id: av, path: {assets_dir / "av.mp4"}}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: red, start: 0, duration: 2, fade_in: 1}}
        - {{clip: av, start: 2, duration: 1, fade_out: 0.4, speed: 2}}
    - type: audio
      clips:
        - {{clip: tone, start: 0, in: 1, duration: 2, fade_in: 0.4, gain: 0.5}}
""")


def _wipes(tmp_path: Path, assets_dir: Path, extra: str = "") -> Path:
    return _write(tmp_path, f"""
bin:
  - {{id: red, path: {assets_dir / "photo.png"}}}
  - {{id: blue, path: {assets_dir / "photo2.png"}}}
scenes:
  - transition: {extra or "wipe"}
    luma: linear_x
    transition_duration: 1
    clips:
      - {{clip: red}}
      - {{clip: blue}}
      - {{clip: red}}
""")


def test_clips_layers_and_fades(tmp_path: Path, assets_dir: Path):
    data, warnings = osp.plan(load_and_resolve(_fades(tmp_path, assets_dir)))
    assert [layer["number"] for layer in data["layers"]] == [1_000_000, 2_000_000]
    red, av, tone = data["clips"]
    assert (red["layer"], red["position"], red["start"], red["end"]) == (1_000_000, 0.0, 0.0, 2.0)
    # the fade in: from the clip's frame 1 (its own count, trim included) to the 25th
    assert red["props"]["alpha"]["Points"] == [{"co": {"X": 1, "Y": 0.0}, "interpolation": 1},
                                               {"co": {"X": 25, "Y": 1.0}, "interpolation": 1}]
    assert red["props"]["has_audio"]["Points"][0]["co"]["Y"] == 0.0  # a video track's clip plays no sound
    # the audio track's: trimmed from 1s, its volume from 0 to the gain
    assert (tone["layer"], tone["start"], tone["end"]) == (2_000_000, 1.0, 3.0)
    assert [p["co"] for p in tone["props"]["volume"]["Points"]] == [{"X": 26, "Y": 0.0}, {"X": 35, "Y": 0.5}]
    assert tone["props"]["has_video"]["Points"][0]["co"]["Y"] == 0.0
    # what OpenShot has no counterpart for is left out, and said
    assert any("`speed`" in w and "av" in w for w in warnings), warnings
    assert data["clips"] and len(set(c["id"] for c in data["clips"])) == 3


def test_the_same_ids_each_build(tmp_path: Path, assets_dir: Path):
    project = load_and_resolve(_fades(tmp_path, assets_dir))
    assert osp.plan(project) == osp.plan(project)


def test_a_wipe_on_the_upper_layer_either_way(tmp_path: Path, assets_dir: Path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    data, _ = osp.plan(load_and_resolve(_wipes(tmp_path, assets_dir)))
    first, second = data["transitions"]
    upper = max(layer["number"] for layer in data["layers"])
    assert first["layer"] == second["layer"] == upper
    # red to blue (blue on top): uncovering it, darker parts first, as MLT's luma
    assert [p["co"]["Y"] for p in first["brightness"]["Points"]] == [1.0, -1.0]
    assert first["picture"].endswith("linear_x.pgm")
    # blue to red (blue on top, going): covering, with the picture turned round
    assert [p["co"]["Y"] for p in second["brightness"]["Points"]] == [-1.0, 1.0]
    assert Path(second["picture"]).exists() and second["picture"].endswith(".png")


def test_a_softer_edge_a_lower_contrast():
    values = [osp.contrast_for(s) for s in (0.0, 0.1, 0.4, 0.8, 1.0)]
    assert values == sorted(values, reverse=True) and values[0] == 30.0 and values[-1] == 0.0


def test_an_openshot_derive_stays_an_effect(tmp_path: Path, assets_dir: Path, monkeypatch):
    """A derive: of libopenshot's effects alone goes to OpenShot as its
    input with the effects on it; with another operation, as the file."""
    import video_as_code.openshot_effects as fx
    from video_as_code import derive

    monkeypatch.setattr(fx, "catalog", lambda: {"Negate": {"props": {}, "description": "", "audio": False}})
    monkeypatch.setattr(derive, "ensure_all", lambda *a, **k: None)
    src = assets_dir / "clip.mp4"
    project = _write(tmp_path, f"""
bin:
  - id: fx
    derive: {{path: {src}, openshot: [{{effect: Negate}}]}}
  - id: cut
    derive: {{path: {src}, trim: [0, 1], openshot: [{{effect: Negate}}]}}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: fx, start: 0, duration: 1}}
        - {{clip: cut, start: 1, duration: 1}}
""")
    # the generated files, stood in by the input itself
    from video_as_code import schema

    doc = schema.load_yaml(project)
    for clip in doc.bin:
        clip.path.parent.mkdir(parents=True, exist_ok=True)
        clip.path.write_bytes(src.read_bytes())
    resolved = schema.validate_and_resolve(doc)
    data, _ = osp.plan(resolved)
    fx_clip, cut_clip = data["clips"]
    assert fx_clip["path"] == str(src.resolve()) and fx_clip["effects"] == [{"effect": "Negate"}]
    assert cut_clip["path"] != str(src.resolve()) and cut_clip["effects"] == []


def _frames(osp_path: Path, frames: list[int], out: Path) -> dict[int, list[tuple[int, int, int]]]:
    """Plays the project with libopenshot's Timeline: frames' left, middle
    and right pixels."""
    from video_as_code import openshot_effects

    script = out / "look.py"
    script.write_text("""
import json, sys, openshot
d = json.load(open(sys.argv[1]))
t = openshot.Timeline(d["width"], d["height"], openshot.Fraction(d["fps"]["num"], d["fps"]["den"]),
                      d["sample_rate"], d["channels"], d["channel_layout"])
t.SetJson(json.dumps(d)); t.Open()
for n in sys.argv[3].split(","):
    t.GetFrame(int(n)).Save(sys.argv[2] + "-" + n + ".png", 1.0)
""", encoding="utf-8")
    subprocess.run([openshot_effects.python(), "-I", str(script), str(osp_path), str(out / "f"), ",".join(map(str, frames))],
                   capture_output=True, check=True)
    result = {}
    for n in frames:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out / f"f-{n}.png"), "-vf", "scale=3:1:flags=neighbor",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        result[n] = [tuple(raw[i:i + 3]) for i in (0, 3, 6)]
    return result


@needs_openshot
def test_played_by_libopenshot_as_the_render(tmp_path: Path, assets_dir: Path, monkeypatch):
    """Red, then a wipe to blue from the left, then one back to red, from
    the left again (the picture turned round on the way out) - as melt's
    render does."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    out = tmp_path / "project.osp"
    assert main(["build", str(_wipes(tmp_path, assets_dir)), "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"]["libopenshot"] and data["files"] and len(data["effects"]) == 2
    first = round(data["effects"][0]["position"] * 25) + 1  # frames count from 1
    second = round(data["effects"][1]["position"] * 25) + 1
    seen = _frames(out, [first - 5, first + 12, first + 30, second + 12, second + 30], tmp_path)
    red = lambda p: p[0] > 200 and p[2] < 60  # noqa: E731
    blue = lambda p: p[2] > 200 and p[0] < 60  # noqa: E731
    assert all(red(p) for p in seen[first - 5])
    assert blue(seen[first + 12][0]) and red(seen[first + 12][2]), seen
    assert all(blue(p) for p in seen[first + 30])
    assert red(seen[second + 12][0]) and blue(seen[second + 12][2]), seen
    assert all(red(p) for p in seen[second + 30])


@needs_openshot
def test_a_dissolve_crosses(tmp_path: Path, assets_dir: Path):
    out = tmp_path / "project.osp"
    assert main(["build", str(_wipes(tmp_path, assets_dir, "dissolve")), "--format", "openshot", "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    blue = next(c for c in data["clips"] if c["title"] == "blue")
    middle = round(blue["position"] * 25) + 1 + 12
    (left, mid, right), = _frames(out, [middle], tmp_path).values()
    assert 90 < mid[0] < 170 and 90 < mid[2] < 170, mid  # half red, half blue


def test_without_libopenshot_says_how_to_get_it(tmp_path: Path, assets_dir: Path, monkeypatch, capsys):
    from video_as_code import openshot_effects

    monkeypatch.setattr(openshot_effects, "python", lambda: None)
    assert main(["build", str(_wipes(tmp_path, assets_dir)), "-o", str(tmp_path / "p.osp")]) == 1
    assert "python3-openshot" in capsys.readouterr().err
