"""parts.py: a project in several files - parts in turn, each its own
kdenlive sequence, overlapping by a transition; their own paths, ids and
presets; --part."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_as_code import cli, xmlgen
from video_as_code.errors import ValidationErrors
from video_as_code.models import WipeTransitionSpec  # noqa: F401  (the specs a transition makes)
from video_as_code.schema import load_and_resolve

MAIN = "project: {id: trip, title: Trip, fps: 25, width: 320, height: 240}\n"


def _still(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240", "-frames:v", "1", str(path)], check=True)


@pytest.fixture
def trip(tmp_path: Path, assets_dir: Path) -> Path:
    """trip.yaml: intro (red then blue, a scene) and mountains/all.yaml,
    which has one part, peak (green then yellow, on a track) - both parts
    with a clip `a`."""
    _still(tmp_path / "media" / "red.png", "red")
    _still(tmp_path / "media" / "blue.png", "blue")
    _still(tmp_path / "mountains" / "media" / "green.png", "green")
    _still(tmp_path / "mountains" / "media" / "yellow.png", "yellow")
    (tmp_path / "trip.yaml").write_text(MAIN + """presets:
  slow: {transition: dissolve, transition_duration: 0.5}
parts:
  - intro.yaml
  - {file: mountains/all.yaml, transition: dissolve, transition_duration: 1}
""", encoding="utf-8")
    (tmp_path / "intro.yaml").write_text("""bin:
  - {id: a, path: media/red.png}
scenes:
  - use: slow
    clips: [{clip: a}, media/blue.png]
    duration: 4
""", encoding="utf-8")
    (tmp_path / "mountains" / "all.yaml").write_text("parts:\n  - peak.yaml\n", encoding="utf-8")
    (tmp_path / "mountains" / "peak.yaml").write_text("""bin:
  - {id: a, path: media/green.png}
  - {id: y, path: media/yellow.png}
timeline:
  tracks:
    - type: video
      clips:
        - {clip: a, start: 0, duration: 2}
        - {clip: y, start: 2, duration: 1}
""", encoding="utf-8")
    return tmp_path / "trip.yaml"


def test_parts_one_after_the_other_overlapping_by_their_transition(trip: Path):
    resolved = load_and_resolve(trip)
    assert resolved.total_frames == 100 + 75 - 25  # (4 s and 3 s, a 1 s dissolve)
    a, b = resolved.tracks
    assert [(p.bin_clip.id, p.start_frame, p.length_frames) for p in a.placements] == [("intro", 0, 100)]
    assert [(p.bin_clip.id, p.start_frame, p.fade_in_frames) for p in b.placements] == [("mountains/all", 75, 25)]


def test_each_part_its_own_paths_and_ids(trip: Path):
    resolved = load_and_resolve(trip)
    intro = resolved.bin_clips[0].sequence
    peak = resolved.bin_clips[1].sequence.bin_clips[0].sequence
    ids = {c.id: c.path.name for c in (*intro.bin_clips, *peak.bin_clips)}
    assert ids["intro:a"] == "red.png" and ids["mountains/all:mountains/peak:a"] == "green.png"
    numeric = [c.numeric_id for c in (*intro.bin_clips, *peak.bin_clips)]
    assert len(numeric) == len(set(numeric))  # (unique in the whole document)


def test_presets_in_force_in_the_parts(trip: Path):
    # intro's scene uses the main file's `slow` (a 0.5 s dissolve between its two clips)
    intro = load_and_resolve(trip, part="intro")
    assert intro.total_frames == 100 and any(p.fade_in_frames == 12 or p.fade_in_frames == 13 for t in intro.tracks for p in t.placements)


def test_one_part_alone(trip: Path):
    peak = load_and_resolve(trip, part="mountains/peak")
    assert peak.total_frames == 75 and peak.part_path == "mountains/peak"
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(trip, part="nope")
    assert "intro, mountains/all, mountains/peak" in str(exc.value)


def test_kdenlive_nested_sequences(trip: Path, tmp_path: Path):
    out = tmp_path / "trip.kdenlive"
    xmlgen.write(load_and_resolve(trip), out)
    root = xmlgen.ET.parse(out).getroot()
    sequences = [t for t in root.iter("tractor") if any(p.get("name") == "kdenlive:producer_type" and p.text == "17" for p in t)]
    assert len(sequences) == 4  # trip, intro, mountains/all, mountains/peak
    uuids = {t.get("id") for t in sequences}
    placed = {e.get("producer") for pl in root.iter("playlist") if pl.get("id") != "main_bin" for e in pl.iter("entry")}
    assert len(uuids & placed) == 3  # each part's sequence placed in the one that contains it, by its uuid
    names = {next(p.text for p in t if p.get("name") == "kdenlive:clipname") for t in sequences}
    assert names == {"Trip", "intro", "mountains/all", "mountains/peak"}
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "main, files, expected",
    [
        (MAIN + "parts: [a.yaml]\n", {"a.yaml": "parts: [b.yaml]\n", "b.yaml": "parts: [a.yaml]\n"}, "a.yaml -> b.yaml -> a.yaml"),
        (MAIN + "parts: [a.yaml]\n", {"a.yaml": MAIN + "scenes: []\n"}, "takes the main file's"),
        (MAIN + "parts: [a.yaml]\nscenes: []\n", {"a.yaml": "scenes: []\n"}, "lists parts only"),
        (MAIN + "parts: [a.yaml, {file: a.yaml, transition: glitch}]\n", {"a.yaml": "bin: []\n"}, "bursts on one clip"),
        (MAIN + "parts: [{file: a.yaml, colour: red}]\n", {"a.yaml": "bin: []\n"}, "parts[0]"),
        (MAIN + "parts: [nowhere.yaml]\n", {}, "nowhere.yaml"),
    ],
)
def test_errors(tmp_path: Path, main: str, files: dict, expected: str):
    (tmp_path / "main.yaml").write_text(main, encoding="utf-8")
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(tmp_path / "main.yaml")
    assert expected in str(exc.value)


def test_transitions_between_parts(trip: Path):
    main = trip.read_text(encoding="utf-8").replace(
        "{file: mountains/all.yaml, transition: dissolve, transition_duration: 1}",
        "{file: mountains/all.yaml, transition: wipe, luma: radial, transition_duration: 1}",
    )
    trip.write_text(main, encoding="utf-8")
    (w,) = load_and_resolve(trip).wipe_transitions
    assert (w.track_a, w.track_b, w.start_frame, w.end_frame, w.luma) == ("Parts A", "Parts B", 75, 99, "radial")


def test_cli_part_and_what_doesnt_go_into_parts_yet(trip: Path, capsys):
    assert cli.main(["build", str(trip), "--part", "intro"]) == 0
    assert (trip.parent / "trip-intro.kdenlive").exists()
    assert cli.main(["render", str(trip), "--proxy", "160x90"]) == 1
    assert "--proxy" in capsys.readouterr().err
    assert cli.main(["fetch", str(trip)]) == 1


def test_rendered_parts_dissolve_into_each_other(trip: Path, tmp_path: Path, melt):
    """Real render: intro ends blue, the mountains begin green; the 1 s
    dissolve between the two parts (3-4 s) mixes them halfway at 3.5 s."""
    out = tmp_path / "trip.kdenlive"
    xmlgen.write(load_and_resolve(trip), out)
    video = tmp_path / "trip.mp4"
    exe, env = melt
    import os
    subprocess.run([exe, str(out), "-consumer", f"avformat:{video}"], env={**os.environ, **env}, capture_output=True, check=True, timeout=120)

    def rgb(t: float) -> tuple[int, int, int]:
        data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=1:1",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return data[0], data[1], data[2]

    assert rgb(2.5)[2] > 200 and rgb(2.5)[1] < 40  # blue: intro's second clip
    r, g, b = rgb(3.5)
    assert 20 < g < 110 and 60 < b < 200, (r, g, b)  # halfway from blue to green
    assert rgb(4.5)[1] > 100 and rgb(4.5)[2] < 40  # green: the mountains' first clip
    assert rgb(5.5)[0] > 200 and rgb(5.5)[1] > 200  # yellow
