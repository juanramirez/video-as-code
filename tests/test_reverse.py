from __future__ import annotations

from pathlib import Path

from kdenlive_generator import reverse, xmlgen
from kdenlive_generator.schema import load_and_resolve


def test_round_trip_preserves_structure(tmp_path: Path, assets_dir: Path):
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: "Mi Proyecto", fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 0.5}}
    - type: video
      clips:
        - {{clip: photo, start: 0.5, duration: 2, fade_in: 0.4, fade_out: 0.4}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"

    data = result.data
    assert data["project"]["fps"] == 25
    assert data["project"]["width"] == 320

    tracks = data["timeline"]["tracks"]
    assert [t["type"] for t in tracks] == ["audio", "video"]

    # 1-frame tolerance (1/25s = 0.04s): rounding to whole frames is
    # intentional (see timecode.py, round-half-up), so a requested 0.5s
    # comes back as 0.52s after the round trip (12.5 frames isn't
    # representable, and exact ties always round up).
    frame = 1 / 25

    audio_clip = tracks[0]["clips"][0]
    assert audio_clip["start"] == 0.0
    assert audio_clip["out"] == 3.0
    assert abs(audio_clip["fade_out"] - 0.5) <= frame

    video_clip = tracks[1]["clips"][0]
    assert abs(video_clip["start"] - 0.5) <= frame
    assert abs(video_clip["out"] - video_clip.get("in", 0.0) - 2.0) <= frame
    assert abs(video_clip["fade_in"] - 0.4) <= frame
    assert abs(video_clip["fade_out"] - 0.4) <= frame


def test_reimported_yaml_is_re_buildable(tmp_path: Path, assets_dir: Path):
    """The result of `kdgen import` must be re-buildable: this validates that
    the produced YAML respects our own schema, not just that it 'looks
    right'."""
    import yaml as pyyaml

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)
    reimported_path = tmp_path / "reimported.yaml"
    reimported_path.write_text(pyyaml.dump(result.data, sort_keys=False), encoding="utf-8")

    # Must not raise: the reimported YAML is valid against our schema.
    resolved2 = load_and_resolve(reimported_path)
    assert resolved2.total_frames == resolved.total_frames


def test_second_round_trip_is_a_fixed_point(tmp_path: Path, assets_dir: Path):
    """`import . build` isn't the identity (frame rounding can change values,
    e.g. 0.5s -> 0.52s), but it must be idempotent: once a YAML has been
    through the cycle once, doing it again shouldn't change it any further.
    Without this, `kdgen build` -> `kdgen import` -> `kdgen build` -> `kdgen
    import` could drift indefinitely instead of settling."""
    import yaml as pyyaml

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: "Mi Proyecto", fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 0.5}}
    - type: video
      clips:
        - {{clip: photo, start: 0.5, duration: 2, fade_in: 0.4, fade_out: 0.4}}
""",
        encoding="utf-8",
    )

    def _round_trip(source_yaml: Path, round_dir: Path) -> dict:
        round_dir.mkdir(parents=True, exist_ok=True)
        resolved = load_and_resolve(source_yaml)
        kdenlive_path = round_dir / "project.kdenlive"
        xmlgen.write(resolved, kdenlive_path)
        result = reverse.import_kdenlive(kdenlive_path)
        assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
        return result.data

    data1 = _round_trip(yaml_path, tmp_path / "round1")
    yaml1_path = tmp_path / "round1" / "reimported.yaml"
    yaml1_path.write_text(pyyaml.dump(data1, sort_keys=False), encoding="utf-8")

    data2 = _round_trip(yaml1_path, tmp_path / "round2")

    assert data2 == data1
