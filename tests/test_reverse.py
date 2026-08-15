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


def test_round_trip_preserves_speed(tmp_path: Path, assets_dir: Path):
    """`speed:` (timewarp) round-trips through a real generated .kdenlive:
    a forward speed change is recognized on import (reverse.py), not just
    generated (xmlgen.py)."""
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
        - {{clip: clip, start: 0, speed: 2.0, preserve_pitch: true}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"

    video_clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert video_clip["speed"] == 2.0
    assert video_clip["preserve_pitch"] is True


def test_round_trip_preserves_project_notes(tmp_path: Path, assets_dir: Path):
    """`notes:` on the project (new this cycle) round-trips through a
    real generated .kdenlive: `kdenlive:documentnotes` on `main_bin`
    (confirmed against kdenlive's own source,
    ProjectManager::slotSaveProject)."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240, notes: "Shot on location, needs color grading."}}
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

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    assert result.data["project"]["notes"] == "Shot on location, needs color grading."


def test_round_trip_preserves_bin_folder(tmp_path: Path, assets_dir: Path):
    """`folder:` on a bin clip (new this cycle) round-trips through a
    real generated .kdenlive: `kdenlive:folder.-1.<id>` on `main_bin`
    (confirmed against kdenlive's own source,
    BinPlaylist::manageBinItemInsertion) - a clip with no `folder:` must
    stay folder-less, not fall into the same folder by accident."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
    folder: "B-Roll"
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 2}}
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    by_id = {c["id"]: c for c in result.data["bin"]}
    assert by_id["photo"]["folder"] == "B-Roll"
    assert "folder" not in by_id["tone"]


def test_round_trip_preserves_custom_track_name(tmp_path: Path, assets_dir: Path):
    """A custom `name:` on a track (new this cycle) round-trips through a
    real generated .kdenlive: `kdenlive:track_name` on the track's own
    tractor (confirmed against kdenlive's own source, TrackModel), not
    the auto-generated `V1`/`A1` fallback."""
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
      name: "Interview"
      clips:
        - {{clip: photo, start: 0, duration: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    assert result.data["timeline"]["tracks"][0]["name"] == "Interview"


def test_round_trip_preserves_hidden_and_locked_track_state(tmp_path: Path, assets_dir: Path):
    """`hidden:`/`locked:` on a track (new this cycle) round-trip through
    a real generated .kdenlive: `hide` (bitmask: bit 1 = hidden) and
    `kdenlive:locked_track` on the track's own tractor (confirmed against
    kdenlive's own source, TrackModel::isHidden/isMute/isLocked)."""
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
      hidden: true
      locked: true
      clips:
        - {{clip: photo, start: 0, duration: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    track = result.data["timeline"]["tracks"][0]
    assert track["hidden"] is True
    assert track["locked"] is True


def test_round_trip_preserves_guides(tmp_path: Path, assets_dir: Path):
    """`guides:` (new this cycle) round-trips through a real generated
    .kdenlive: the JSON array format (`{"pos", "comment", "type"}`) was
    confirmed against kdenlive's own source (MarkerListModel::toJson),
    not modeled by analogy."""
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
guides:
  - {{at: 0.5, text: "First"}}
  - {{at: 1.5, text: "Second", category: 3}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    guides = result.data["guides"]
    assert len(guides) == 2
    # 1-frame tolerance (1/25s = 0.04s), same rounding note as
    # test_round_trip_preserves_structure: 0.5s isn't a whole number of
    # frames at 25fps.
    frame = 1 / 25
    assert abs(guides[0]["at"] - 0.5) <= frame
    assert guides[0]["text"] == "First"
    assert "category" not in guides[0]
    assert abs(guides[1]["at"] - 1.5) <= frame
    assert guides[1]["text"] == "Second"
    assert guides[1]["category"] == 3


def test_round_trip_preserves_wipe_transition(tmp_path: Path, assets_dir: Path):
    """A `scenes:` wipe transition (new this cycle) round-trips through a
    real generated .kdenlive into the low-level `transitions:` YAML key -
    reconstructing the original `scenes:` sugar (clip list, pacing) isn't
    attempted, but it's no longer silently lost to `x_kdenlive_raw`
    either, closing the gap `wipe`'s own ROADMAP entry flagged."""
    import yaml as pyyaml

    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: wipe
    transition_duration: 1.0
    luma: diagonal
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    transitions = result.data.get("transitions")
    assert transitions, f"expected a reconstructed transitions: entry, got raw_items={result.raw_items}"
    assert len(transitions) == 1
    t = transitions[0]
    assert t["type"] == "wipe"
    assert t["luma"] == "diagonal"
    track_names = {tr["name"] for tr in result.data["timeline"]["tracks"]}
    assert t["track_a"] in track_names
    assert t["track_b"] in track_names

    reimported_path = tmp_path / "reimported.yaml"
    reimported_path.write_text(pyyaml.dump(result.data), encoding="utf-8")
    load_and_resolve(reimported_path)  # must not raise: re-buildable, not just plausible-looking


def test_round_trip_preserves_affine_transition(tmp_path: Path, assets_dir: Path):
    """Same as `test_round_trip_preserves_wipe_transition`, for the
    `affine`-based `zoom` transition (new this cycle)."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: affine
    style: zoom
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    transitions = result.data.get("transitions")
    assert transitions, f"expected a reconstructed transitions: entry, got raw_items={result.raw_items}"
    assert len(transitions) == 1
    assert transitions[0]["type"] == "affine"
    assert transitions[0]["style"] == "zoom"


def test_round_trip_preserves_negative_speed(tmp_path: Path, assets_dir: Path):
    """Reverse playback (`speed:` negative, new this cycle) round-trips
    through a real generated .kdenlive the same way forward speed does -
    it used to fall to `x_kdenlive_raw` (see the ROADMAP's "gaps the code
    already flags" history), not anymore."""
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
        - {{clip: clip, start: 0, speed: -1.0}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"

    video_clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert video_clip["speed"] == -1.0


def test_round_trip_preserves_title(tmp_path: Path):
    """`type: title` bin clips round-trip through a real generated
    .kdenlive: reverse.py reads the referenced .kdenlivetitle file back
    (titles.parse_title_xml) and reconstructs the same text/font/size,
    not just recognizing that a title clip exists. Also a regression test
    for a real bug: `Importer.preregister_bin_order` used to always call
    the generic `register_bin_clip` for every main_bin entry regardless
    of kind, which silently registered the title with no `type`/`text` at
    all - by the time `convert_playlist` reached the actual timeline
    entry and tried to register it as a title, the resource was already
    registered (as a bare path), so its title data was never attached."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        """
project: {id: p1, title: T, fps: 25, width: 320, height: 240}
bin:
  - id: title1
    type: title
    text: "Hello World"
    font_size: 36
timeline:
  tracks:
    - type: video
      clips:
        - {clip: title1, start: 0, duration: 2}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"

    title_bin_entry = result.data["bin"][0]
    assert title_bin_entry["type"] == "title"
    assert title_bin_entry["text"] == "Hello World"
    assert title_bin_entry["font_size"] == 36


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


def test_second_build_produces_identical_kdenlive_xml(tmp_path: Path, assets_dir: Path):
    """Stronger than test_second_round_trip_is_a_fixed_point: that test only
    compares the intermediate YAML each cycle produces. This one compares
    the actual generated `.kdenlive` XML byte-for-byte across two cycles,
    confirming xmlgen.py's output is truly stable once round-tripped once —
    not just that the YAML representation happens to converge while the
    XML it produces could still drift (different filter ordering, stray
    property, etc.)."""
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

    # Both YAMLs live directly in tmp_path (not a subdirectory each), so the
    # `root` attribute in the generated XML (derived from the YAML's own
    # directory, not the .kdenlive output path) matches for both builds and
    # doesn't need to be filtered out to compare the rest.
    #
    # The .kdenlive's project id ("p1") is never written anywhere readable
    # in the XML, so on import reverse.py has no way to recover it and falls
    # back to the output file's own stem instead (see Importer.run). That
    # id seeds every deterministic UUID in the file (kdenlive:control_uuid,
    # the sequence UUID, the document id), so naming this file anything
    # other than "p1.kdenlive" would make the second build's UUIDs diverge
    # from the first even though nothing about the timeline changed. This
    # mirrors the CLI's own default naming (`kdgen build p1.yaml` ->
    # `p1.kdenlive`, `kdgen import p1.kdenlive` -> `p1.yaml`), which keeps
    # the stem consistent automatically — it's only a foot-gun if you
    # rename the file in between.
    resolved1 = load_and_resolve(yaml_path)
    kdenlive1_path = tmp_path / "p1.kdenlive"
    xmlgen.write(resolved1, kdenlive1_path)

    result1 = reverse.import_kdenlive(kdenlive1_path)
    assert result1.raw_items == [], f"there should be no unrecognized items: {result1.raw_items}"
    yaml1_path = tmp_path / "reimported.yaml"
    yaml1_path.write_text(pyyaml.dump(result1.data, sort_keys=False), encoding="utf-8")

    resolved2 = load_and_resolve(yaml1_path)
    kdenlive2_path = tmp_path / "round2.kdenlive"
    xmlgen.write(resolved2, kdenlive2_path)

    assert kdenlive1_path.read_text() == kdenlive2_path.read_text()
