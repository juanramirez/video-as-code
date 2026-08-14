from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_generator import xmlgen
from kdenlive_generator.schema import load_and_resolve


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
