from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_as_code import reverse, xmlgen
from kdenlive_as_code.schema import load_and_resolve


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


def test_round_trip_preserves_audio_muted_track(tmp_path: Path, assets_dir: Path):
    """`audio_muted:` on a track (new this cycle) round-trips through a
    real generated .kdenlive: `hide`'s bit 2 (`TrackModel::isMute`) -
    distinct from `hidden:` (bit 1) and from the per-clip `mute:`
    already supported."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      audio_muted: true
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
    track = result.data["timeline"]["tracks"][0]
    assert track["audio_muted"] is True
    assert "hidden" not in track


def test_round_trip_preserves_pan(tmp_path: Path, assets_dir: Path):
    """`pan:` on an audio track (v0.3) round-trips through a real
    generated .kdenlive (new this cycle) - reverse.py previously only
    read per-*entry* filters, never a track tractor's own `panner`
    filter."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      pan: -0.5
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
    assert result.data["timeline"]["tracks"][0]["pan"] == -0.5


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


@pytest.mark.parametrize("transition", ["iris", "flip"])
def test_round_trip_preserves_reveal_style(tmp_path: Path, assets_dir: Path, transition: str):
    """`iris`/`flip` (new this cycle) round-trip through a real generated
    .kdenlive: unlike `wipe`/`affine`, these are single-clip filters
    (`frei0r.alphaspot`/`frei0r.3dflippo`) on a playlist entry, not
    `<transition>` elements - recognized by `convert_clip_filter` into a
    new low-level `reveal:` field on the clip placement (paired with the
    `fade_in`/`fade_out` it's actually keyframed over), not the
    top-level `transitions:` key `wipe`/`affine` use."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: {transition}
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

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    top_track = result.data["timeline"]["tracks"][1]
    top_clip = top_track["clips"][0]
    assert top_clip["reveal"] == transition
    assert abs(top_clip["fade_in"] - 1.0) <= 1 / 25

    # And the reconstructed YAML should itself be re-buildable.
    import yaml as pyyaml

    reimported_path = tmp_path / "reimported.yaml"
    reimported_path.write_text(pyyaml.dump(result.data), encoding="utf-8")
    load_and_resolve(reimported_path)


def test_round_trip_preserves_glitch(tmp_path: Path, assets_dir: Path):
    """`glitch` (new this cycle, frei0r.glitch0r) round-trips through a
    real generated .kdenlive too - unlike `iris`/`flip`, every clip (not
    just an alternating "top" one) carries `reveal: glitch`, since it's
    a plain single-track cut dressing, not a two-source reveal."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: glitch
    transition_duration: 0.4
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

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    tracks = result.data["timeline"]["tracks"]
    assert len(tracks) == 1
    clips = tracks[0]["clips"]
    assert len(clips) == 2
    assert clips[0]["reveal"] == "glitch"
    assert abs(clips[0]["fade_out"] - 0.4) <= 1 / 25
    assert "fade_in" not in clips[0]
    assert clips[1]["reveal"] == "glitch"
    assert abs(clips[1]["fade_in"] - 0.4) <= 1 / 25
    assert "fade_out" not in clips[1]

    import yaml as pyyaml

    reimported_path = tmp_path / "reimported.yaml"
    reimported_path.write_text(pyyaml.dump(result.data), encoding="utf-8")
    load_and_resolve(reimported_path)


@pytest.mark.parametrize("transition", ["delaygrab", "cairogradient"])
def test_round_trip_preserves_burst_transitions(tmp_path: Path, assets_dir: Path, transition: str):
    """`delaygrab`/`cairogradient` (v0.6, same BURST_TRANSITIONS shape
    `glitch` uses) round-trip through a real generated .kdenlive the same
    way `glitch` does - every clip (not just an alternating "top" one)
    carries `reveal: {transition}`, and `delaygrab` specifically has no
    adjustable parameters at all to get wrong on the way back."""
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: {transition}
    transition_duration: 0.4
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

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    tracks = result.data["timeline"]["tracks"]
    assert len(tracks) == 1
    clips = tracks[0]["clips"]
    assert len(clips) == 2
    assert clips[0]["reveal"] == transition
    assert abs(clips[0]["fade_out"] - 0.4) <= 1 / 25
    assert "fade_in" not in clips[0]
    assert clips[1]["reveal"] == transition
    assert abs(clips[1]["fade_in"] - 0.4) <= 1 / 25
    assert "fade_out" not in clips[1]

    import yaml as pyyaml

    reimported_path = tmp_path / "reimported.yaml"
    reimported_path.write_text(pyyaml.dump(result.data), encoding="utf-8")
    load_and_resolve(reimported_path)


def test_round_trip_preserves_chroma_key(tmp_path: Path, assets_dir: Path):
    """`chroma_key:`/`chroma_key_distance:` (new this cycle, v0.5's
    headline feature) round-trip through a real generated .kdenlive:
    unlike `reveal:`, color/distance are plain user-facing values (not a
    keyframed animation this generator itself constructs), so any value
    round-trips, not just ones this generator would have produced -
    `chroma_key_distance` is only emitted when it differs from the
    0.35 default, matching how other optional fields stay sparse."""
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
        - {{clip: photo, start: 0, duration: 1, chroma_key: "#00ff00", chroma_key_distance: 0.5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert clip["chroma_key"] == "#00ff00"
    assert clip["chroma_key_distance"] == 0.5


def test_round_trip_preserves_overlay_text(tmp_path: Path, assets_dir: Path):
    """`overlay_text:` (new this cycle, MLT's own `dynamictext` filter)
    round-trips through a real generated .kdenlive - like `chroma_key:`,
    plain user-facing values round-trip freely; only the fixed centered/
    full-frame layout `_add_overlay_text_filter` always writes has to
    match exactly, since `geometry`/`halign`/`valign` aren't settable
    fields in this schema."""
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
        - {{clip: photo, start: 0, duration: 1, overlay_text: "#timecode#", overlay_font_size: 32}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert clip["overlay_text"] == "#timecode#"
    assert clip["overlay_font_size"] == 32
    assert "overlay_font" not in clip  # default "Sans" stays sparse


def test_round_trip_preserves_ken_burns(tmp_path: Path, assets_dir: Path):
    """`ken_burns_from:`/`ken_burns_to:` (new this cycle, v0.6's headline
    feature) round-trip through a real generated .kdenlive - like
    `chroma_key:`, the rect *values* are plain user-facing numbers and
    round-trip freely; only the two-keyframe/percent-units/opacity-100/
    no-distort shape `_add_ken_burns_filter` always writes has to match
    exactly, since that structure isn't a settable field in this
    schema."""
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
        - clip: photo
          start: 0
          duration: 1
          ken_burns_from: [0, 0, 100, 100]
          ken_burns_to: [-25, -25, 150, 150]
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert clip["ken_burns_from"] == [0.0, 0.0, 100.0, 100.0]
    assert clip["ken_burns_to"] == [-25.0, -25.0, 150.0, 150.0]


def test_round_trip_preserves_focus_rect(tmp_path: Path, assets_dir: Path):
    """`focus_rect:`/`focus_blur:` (v0.6, MLT's own `pillar_echo` filter)
    round-trip through a real generated .kdenlive - like `chroma_key:`,
    plain user-facing values round-trip freely (no keyframed animation
    this generator itself constructs)."""
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
        - clip: photo
          start: 0
          duration: 1
          focus_rect: [35, 35, 30, 30]
          focus_blur: 20
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert clip["focus_rect"] == [35.0, 35.0, 30.0, 30.0]
    assert clip["focus_blur"] == 20.0


def test_round_trip_preserves_position_keyframes(tmp_path: Path, assets_dir: Path):
    """`position_keyframes:` (v0.7, animated PiP) round-trips through a
    real generated .kdenlive - a multi-keyframe `qtblend` `rect` is a
    plain, free-form user-facing value (no fixed generator-constructed
    shape to match exactly, unlike `ken_burns_from:`/`ken_burns_to:`),
    so any valid multi-keyframe rect recovers cleanly."""
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
        - clip: photo
          start: 0
          duration: 2.0
          position_keyframes:
            - {{at: 0, position: [0, 0], size: [60, 60]}}
            - {{at: 1, position: [260, 180], size: [60, 60]}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert clip["position_keyframes"] == [
        {"at": 0.0, "position": [0.0, 0.0], "size": [60.0, 60.0]},
        {"at": 1.0, "position": [260.0, 180.0], "size": [60.0, 60.0]},
    ]


def test_round_trip_preserves_crop(tmp_path: Path, assets_dir: Path):
    """`crop:` (v0.7, MLT's own `crop` filter via the two-instance
    workaround - see `_add_crop_filters`'s docstring in xmlgen.py)
    round-trips through a real generated .kdenlive: `_match_crop_filters`
    recognizes the passive+active filter pair it itself produces and
    reconstructs the same top/right/bottom/left dict, while the qtblend
    refill filter behind it stays silently recognized as the ordinary
    full-canvas default (crop is mutually exclusive with position/size,
    so it always is)."""
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
        - clip: photo
          start: 0
          duration: 2.0
          crop: {{top: 10, right: 20, bottom: 0, left: 5}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert result.raw_items == [], f"there should be no unrecognized items: {result.raw_items}"
    clip = result.data["timeline"]["tracks"][0]["clips"][0]
    assert clip["crop"] == {"top": 10.0, "right": 20.0, "bottom": 0.0, "left": 5.0}


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


def test_speed_ramp_falls_to_raw_items_instead_of_silently_lost(tmp_path: Path, assets_dir: Path):
    """`speed_ramp:` (v0.7's headline feature, MLT's `timeremap` link) is
    NOT reconstructed on import - regression test for a real gap this
    surfaced: `classify_producer` only ever reads a chain's own
    `resource`/`mlt_service`, never a child `<link>`, so without an
    explicit check a ramped clip would silently come back as an
    ordinary, unramped clip instead of being flagged - a real, silent
    data loss, not just an unsupported-but-flagged case like reverse
    speed used to be. Falls to `x_kdenlive_raw` instead, same as
    anything else this generator can't cleanly invert."""
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
        - clip: clip
          start: 0
          duration: 1.0
          speed_ramp:
            - {{at: 0, speed: 2.0}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)

    result = reverse.import_kdenlive(kdenlive_path)

    assert len(result.raw_items) == 1, f"expected exactly one flagged item, got: {result.raw_items}"
    tracks = result.data["timeline"]["tracks"]
    clips = [c for t in tracks for c in t["clips"]]
    assert clips == [], f"expected no (incorrectly) recognized clips - the ramp must not silently become a plain clip, got: {clips}"


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
    """The result of `kdenlive-as-code import` must be re-buildable: this validates that
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
    Without this, `kdenlive-as-code build` -> `kdenlive-as-code import` -> `kdenlive-as-code build` -> `kdenlive-as-code
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
    # mirrors the CLI's own default naming (`kdenlive-as-code build p1.yaml` ->
    # `p1.kdenlive`, `kdenlive-as-code import p1.kdenlive` -> `p1.yaml`), which keeps
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
