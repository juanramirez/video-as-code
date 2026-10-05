from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from video_as_code import xmlgen
from video_as_code.schema import load_and_resolve


def _project_yaml(assets_dir: Path) -> str:
    return f"""
project: {{id: p1, title: "Test Project", fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: clip
    path: {assets_dir / "clip.mp4"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 0.5}}
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5, fade_in: 0.5}}
        - {{clip: clip, start: 1.5, duration: 2}}
"""


def _build(tmp_path: Path, assets_dir: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(_project_yaml(assets_dir), encoding="utf-8")
    resolved = load_and_resolve(yaml_path)
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, out_path)
    return out_path


def test_generated_file_is_well_formed_xml(tmp_path: Path, assets_dir: Path):
    out_path = _build(tmp_path, assets_dir)
    tree = ET.parse(out_path)  # raises if not valid XML
    assert tree.getroot().tag == "mlt"


def test_audio_fade_in_level_sets_starting_gain(tmp_path: Path, assets_dir: Path):
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: "Test Project", fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_in: 1.0, fade_in_level: 0.4}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, out_path)
    root = ET.parse(out_path).getroot()

    fadein_filters = [
        f
        for f in root.iter("filter")
        if any(p.get("name") == "kdenlive_id" and p.text == "fadein" for p in f.iter("property"))
    ]
    assert len(fadein_filters) == 1
    gain = next(p.text for p in fadein_filters[0].iter("property") if p.get("name") == "gain")
    assert float(gain) == 0.4


def test_audio_fade_out_level_sets_ending_volume(tmp_path: Path, assets_dir: Path):
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: "Test Project", fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 1.0, fade_out_level: 0.5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, out_path)
    root = ET.parse(out_path).getroot()

    fadeout_filters = [
        f
        for f in root.iter("filter")
        if any(p.get("name") == "kdenlive_id" and p.text == "fadeout" for p in f.iter("property"))
    ]
    assert len(fadeout_filters) == 1
    end = next(p.text for p in fadeout_filters[0].iter("property") if p.get("name") == "end")
    assert float(end) == 0.5


def test_gain_adds_flat_volume_filter(tmp_path: Path, assets_dir: Path):
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: "Test Project", fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, gain: 1.5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, out_path)
    root = ET.parse(out_path).getroot()

    entry = next(root.iter("entry"))
    volume_filters = [
        f
        for f in entry.iter("filter")
        if any(p.get("name") == "mlt_service" and p.text == "volume" for p in f.iter("property"))
    ]
    gain_filters = [
        f
        for f in volume_filters
        if not any(p.get("name") == "kdenlive_id" for p in f.iter("property"))
    ]
    assert len(gain_filters) == 1
    gain = next(p.text for p in gain_filters[0].iter("property") if p.get("name") == "gain")
    assert float(gain) == 1.5


def test_all_producer_references_resolve(tmp_path: Path, assets_dir: Path):
    out_path = _build(tmp_path, assets_dir)
    root = ET.parse(out_path).getroot()

    defined_ids = {el.get("id") for el in root.iter() if el.get("id") is not None}
    referenced_ids = set()
    for el in root.iter():
        if el.tag in ("entry", "track") and el.get("producer"):
            referenced_ids.add(el.get("producer"))

    missing = referenced_ids - defined_ids
    assert not missing, f"references to nonexistent producer/tractor: {missing}"


def test_structure_counts(tmp_path: Path, assets_dir: Path):
    out_path = _build(tmp_path, assets_dir)
    root = ET.parse(out_path).getroot()

    # 2 tracks (audio + video) => 1 black producer + 2 track tractors
    # referenced from the sequence, plus 2 transitions (mix + qtblend).
    sequences = [t for t in root.findall("tractor") if t.find("./property[@name='kdenlive:uuid']") is not None]
    assert len(sequences) == 1
    sequence = sequences[0]
    assert len(sequence.findall("track")) == 3  # black + audio + video
    assert len(sequence.findall("transition")) == 2

    transition_services = {
        t.find("./property[@name='mlt_service']").text for t in sequence.findall("transition")
    }
    assert transition_services == {"mix", "qtblend"}

    main_bin = root.find("./playlist[@id='main_bin']")
    assert main_bin is not None
    # 1 entry for the sequence + 1 per bin clip (3)
    assert len(main_bin.findall("entry")) == 4


def test_deterministic_output(tmp_path: Path, assets_dir: Path):
    out1 = _build(tmp_path / "a", assets_dir)
    out2 = _build(tmp_path / "b", assets_dir)
    # Asset paths are the same (assets_dir is session-scoped), so only the
    # project's root directory changes -> filter that line out.
    text1 = out1.read_text().replace(str(tmp_path / "a"), "")
    text2 = out2.read_text().replace(str(tmp_path / "b"), "")
    assert text1 == text2


def test_speed_ramp_source_in_offsets_time_map(tmp_path: Path, assets_dir: Path):
    """`source_in:` with `speed_ramp:` shifts every `time_map` value by
    that many source seconds (values are absolute source seconds), so the
    first output frame reads at source_in instead of 0."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: clip
    path: {assets_dir / "clip.mp4"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: clip, start: 0, duration: 1.0, source_in: 1.0, speed_ramp: [{{at: 0, speed: 1.0}}, {{at: 0.4, speed: 2.0}}]}}
""",
        encoding="utf-8",
    )
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(load_and_resolve(yaml_path), out_path)
    link = ET.parse(out_path).getroot().find(".//link")
    time_map = next(p.text for p in link.findall("property") if p.get("name") == "time_map")
    values = dict(kv.split("=") for kv in time_map.split(";"))
    assert float(values["0"]) == 1.0
    assert abs(float(values["10"]) - 1.4) < 1e-6  # 0.4s at 1x
    assert abs(float(values["15"]) - 1.8) < 1e-6  # then 0.2s at 2x
