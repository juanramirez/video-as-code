"""Generates the XML (MLT format + kdenlive extensions) from an already
validated ResolvedProject.

The structure replicates what kdenlive 24-26.x produces when saving a real
project (see README for more context): a black background producer, each
track wrapped in a tractor with two playlists (to support kdenlive's "mix"
feature), a sequence tractor with the compositing transitions, the project
bin (`main_bin`), and a final wrapper tractor.

Anything that isn't essential for kdenlive to open and edit the project
(file hashes, probing metadata, thumbnails...) is deliberately omitted:
kdenlive recomputes them on its own when opening the project. This keeps
the generator simple and the output deterministic.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from .ids import deterministic_document_id, deterministic_uuid
from .models import ResolvedPlacement, ResolvedProject, ResolvedTrack

MLT_VERSION = "7.38.0"
KDENLIVE_VERSION = "26.04.3"
DOC_VERSION = "1.1"


def _prop(parent: ET.Element, name: str, value: object = "") -> ET.Element:
    el = ET.SubElement(parent, "property", {"name": name})
    if value is not None:
        el.text = str(value)
    return el


def _gcd_ratio(w: int, h: int) -> tuple[int, int]:
    g = math.gcd(w, h) or 1
    return w // g, h // g


class _IdCounters:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        n = self._counts.get(prefix, 0)
        self._counts[prefix] = n + 1
        return f"{prefix}{n}"


def _service_for(kind: str, *, fast: bool) -> str:
    if kind == "image":
        return "qimage"
    return "avformat-novalidate" if fast else "avformat"


def _resource_for(placement_kind: str, path: Path) -> str:
    return str(path)


def _build_media_element(
    ids: _IdCounters,
    *,
    path: Path,
    kind: str,
    in_frame: int,
    out_frame: int,
    fast: bool,
    numeric_id: int,
    control_uuid: str,
) -> ET.Element:
    tag = "chain"
    xml_id = ids.next("chain")
    el = ET.Element(tag, {"id": xml_id, "in": str(in_frame), "out": str(out_frame)})
    service = _service_for(kind, fast=fast)
    if kind == "image":
        _prop(el, "length", out_frame + 1)
        _prop(el, "eof", "pause")
        _prop(el, "resource", _resource_for(kind, path))
        _prop(el, "aspect_ratio", 1)
        _prop(el, "mlt_service", service)
    else:
        _prop(el, "length", out_frame + 1)
        _prop(el, "eof", "pause")
        _prop(el, "resource", _resource_for(kind, path))
        _prop(el, "mlt_service", service)
        _prop(el, "seekable", 1)
    _prop(el, "kdenlive:folderid", -1)
    _prop(el, "kdenlive:id", numeric_id)
    _prop(el, "kdenlive:control_uuid", control_uuid)
    return el, xml_id


def _add_video_default_filter(ids: _IdCounters, entry: ET.Element, width: int, height: int, rect) -> None:
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    if rect is not None:
        x, y, w, h = rect
    else:
        x, y, w, h = 0, 0, width, height
    _prop(f, "rotate_center", 1)
    _prop(f, "mlt_service", "qtblend")
    _prop(f, "kdenlive_id", "qtblend")
    _prop(f, "rect", f"0={x:g} {y:g} {w:g} {h:g} 1")
    _prop(f, "rotation", "0=0")
    _prop(f, "compositing", 0)
    _prop(f, "distort", 1)
    _prop(f, "kdenlive:collapsed", 0)


def _add_video_fade_filters(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    p_in, p_out = placement.in_frame, placement.out_frame
    if placement.fade_in_frames > 0:
        L = placement.fade_in_frames
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "out": str(p_in + L - 1)})
        _prop(f, "start", 1)
        _prop(f, "level", 1)
        _prop(f, "mlt_service", "brightness")
        _prop(f, "kdenlive_id", "fade_from_black")
        _prop(f, "alpha", f"0=0;{L - 1}=1")
        _prop(f, "kdenlive:collapsed", 0)
    if placement.fade_out_frames > 0:
        L = placement.fade_out_frames
        f = ET.SubElement(
            entry,
            "filter",
            {"id": ids.next("filter"), "in": str(p_out - L + 1), "out": str(p_out)},
        )
        _prop(f, "start", 1)
        _prop(f, "level", 1)
        _prop(f, "mlt_service", "brightness")
        _prop(f, "kdenlive_id", "fade_to_black")
        _prop(f, "alpha", f"0=1;{L - 1}=0")
        _prop(f, "kdenlive:collapsed", 0)


def _add_audio_fade_filters(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Matches kdenlive's own "Fade in"/"Fade out" audio effects: mlt_service
    "volume", kdenlive_id "fadein"/"fadeout", plain linear `gain`/`end`
    (0..1) rather than a dB-keyframed `level`. Confirmed against the effect
    definitions and filter presets shipped with kdenlive itself
    (share/kdenlive/effects/fadein.xml, share/mlt-7/presets/filter/volume/
    fade_in) — not just modeled by analogy with the video fade filter, as
    the previous version of this function was (it used a nonexistent
    kdenlive_id and a dB keyframe string on a property, "gain", that MLT's
    own filter docs mark as ignoring animation when "level" isn't set)."""
    p_in, p_out = placement.in_frame, placement.out_frame
    if placement.fade_in_frames > 0:
        L = placement.fade_in_frames
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "out": str(p_in + L - 1)})
        _prop(f, "mlt_service", "volume")
        _prop(f, "kdenlive_id", "fadein")
        _prop(f, "gain", 0)
        _prop(f, "end", 1)
        _prop(f, "kdenlive:collapsed", 0)
    if placement.fade_out_frames > 0:
        L = placement.fade_out_frames
        f = ET.SubElement(
            entry,
            "filter",
            {"id": ids.next("filter"), "in": str(p_out - L + 1), "out": str(p_out)},
        )
        _prop(f, "mlt_service", "volume")
        _prop(f, "kdenlive_id", "fadeout")
        _prop(f, "gain", 1)
        _prop(f, "end", 0)
        _prop(f, "kdenlive:collapsed", 0)


def _build_track(
    root: ET.Element,
    ids: _IdCounters,
    project: ResolvedProject,
    track: ResolvedTrack,
    control_uuid_for: dict,
) -> str:
    """Adds the chains, the two playlists, and the wrapping tractor of a
    track to `root`. Returns the track tractor's id."""
    is_audio = track.kind == "audio"
    playlist_main = ET.Element("playlist", {"id": ids.next("playlist")})
    if is_audio:
        _prop(playlist_main, "kdenlive:audio_track", 1)

    cursor = 0
    for placement in track.placements:
        if placement.start_frame > cursor:
            ET.SubElement(playlist_main, "blank", {"length": str(placement.start_frame - cursor)})
        el, xml_id = _build_media_element(
            ids,
            path=placement.bin_clip.path,
            kind=placement.bin_clip.kind,
            in_frame=placement.in_frame,
            out_frame=placement.out_frame,
            fast=True,
            numeric_id=placement.bin_clip.numeric_id,
            control_uuid=control_uuid_for[placement.bin_clip.id],
        )
        root.append(el)
        entry = ET.SubElement(
            playlist_main,
            "entry",
            {"producer": xml_id, "in": str(placement.in_frame), "out": str(placement.out_frame)},
        )
        _prop(entry, "kdenlive:id", placement.bin_clip.numeric_id)
        if is_audio:
            _add_audio_fade_filters(ids, entry, placement)
        else:
            _add_video_fade_filters(ids, entry, placement)
            _add_video_default_filter(ids, entry, project.width, project.height, placement.rect)
        cursor = placement.start_frame + placement.length_frames

    root.append(playlist_main)

    playlist_secondary = ET.Element("playlist", {"id": ids.next("playlist")})
    if is_audio:
        _prop(playlist_secondary, "kdenlive:audio_track", 1)
    root.append(playlist_secondary)

    track_tractor = ET.Element("tractor", {"id": ids.next("tractor")})
    if is_audio:
        _prop(track_tractor, "kdenlive:audio_track", 1)
    _prop(track_tractor, "kdenlive:trackheight", 69)
    _prop(track_tractor, "kdenlive:timeline_active", 1)
    _prop(track_tractor, "kdenlive:collapsed", 0)
    _prop(track_tractor, "kdenlive:thumbs_format", None)
    _prop(track_tractor, "kdenlive:audio_rec", None)
    hide = "video" if is_audio else "audio"
    ET.SubElement(track_tractor, "track", {"hide": hide, "producer": playlist_main.get("id")})
    ET.SubElement(track_tractor, "track", {"hide": hide, "producer": playlist_secondary.get("id")})
    if is_audio:
        f1 = ET.SubElement(track_tractor, "filter", {"id": ids.next("filter")})
        _prop(f1, "window", 75)
        _prop(f1, "max_gain", "20dB")
        _prop(f1, "mlt_service", "volume")
        _prop(f1, "internal_added", 237)
        _prop(f1, "disable", 1)
        f2 = ET.SubElement(track_tractor, "filter", {"id": ids.next("filter")})
        _prop(f2, "channel", -1)
        _prop(f2, "mlt_service", "panner")
        _prop(f2, "internal_added", 237)
        _prop(f2, "start", 0.5)
        _prop(f2, "disable", 1)
        f3 = ET.SubElement(track_tractor, "filter", {"id": ids.next("filter")})
        _prop(f3, "iec_scale", 0)
        _prop(f3, "mlt_service", "audiolevel")
        _prop(f3, "dbpeak", 1)
        _prop(f3, "disable", 1)
    root.append(track_tractor)

    return track_tractor.get("id")


def generate(project: ResolvedProject) -> ET.ElementTree:
    root = ET.Element(
        "mlt",
        {
            "LC_NUMERIC": "C",
            "producer": "main_bin",
            "root": str(project.source_dir),
            "version": MLT_VERSION,
        },
    )

    dar_n, dar_d = _gcd_ratio(project.width, project.height)
    ET.SubElement(
        root,
        "profile",
        {
            "colorspace": str(project.colorspace),
            "description": f"{project.width}x{project.height} {project.fps.fps:g} fps",
            "display_aspect_den": str(dar_d),
            "display_aspect_num": str(dar_n),
            "frame_rate_den": str(project.fps.den),
            "frame_rate_num": str(project.fps.num),
            "height": str(project.height),
            "progressive": "1",
            "sample_aspect_den": "1",
            "sample_aspect_num": "1",
            "width": str(project.width),
        },
    )

    ids = _IdCounters()

    total_frames = max(project.total_frames, 1)
    black_bg = ET.SubElement(root, "producer", {"id": "producer_black", "in": "0", "out": str(total_frames - 1)})
    _prop(black_bg, "length", 2147483647)
    _prop(black_bg, "eof", "continue")
    _prop(black_bg, "resource", "black")
    _prop(black_bg, "aspect_ratio", 1)
    _prop(black_bg, "mlt_service", "color")
    _prop(black_bg, "kdenlive:playlistid", "black_track")
    _prop(black_bg, "mlt_image_format", "rgba")

    control_uuid_for = {c.id: deterministic_uuid(project.project_id, "clip", c.id) for c in project.bin_clips}

    track_tractor_ids: list[tuple[str, str]] = []  # (kind, xml_id)
    for track in project.tracks:
        tid = _build_track(root, ids, project, track, control_uuid_for)
        track_tractor_ids.append((track.kind, tid))

    sequence_uuid = deterministic_uuid(project.project_id, "sequence")
    sequence = ET.Element("tractor", {"id": sequence_uuid, "in": "0", "out": str(total_frames - 1)})
    has_audio = any(t.kind == "audio" for t in project.tracks)
    has_video = any(t.kind == "video" for t in project.tracks)
    video_positions = [i + 1 for i, (kind, _) in enumerate(track_tractor_ids) if kind == "video"]
    audio_positions = [i + 1 for i, (kind, _) in enumerate(track_tractor_ids) if kind == "audio"]

    _prop(sequence, "kdenlive:uuid", sequence_uuid)
    _prop(sequence, "kdenlive:clipname", project.title)
    _prop(sequence, "kdenlive:sequenceproperties.hasAudio", int(has_audio))
    _prop(sequence, "kdenlive:sequenceproperties.hasVideo", int(has_video))
    _prop(sequence, "kdenlive:sequenceproperties.activeTrack", len(track_tractor_ids))
    _prop(sequence, "kdenlive:sequenceproperties.tracksCount", len(track_tractor_ids))
    _prop(sequence, "kdenlive:sequenceproperties.documentuuid", sequence_uuid)
    _prop(sequence, "kdenlive:control_uuid", sequence_uuid)
    _prop(sequence, "kdenlive:duration", total_frames)
    _prop(sequence, "kdenlive:maxduration", total_frames)
    _prop(sequence, "kdenlive:producer_type", 17)
    sequence_numeric_id = len(project.bin_clips) + 1
    _prop(sequence, "kdenlive:id", sequence_numeric_id)
    _prop(sequence, "kdenlive:clip_type", 0)
    _prop(sequence, "kdenlive:folderid", 2)
    _prop(sequence, "kdenlive:sequenceproperties.audioTarget", audio_positions[0] if audio_positions else -1)
    _prop(sequence, "kdenlive:sequenceproperties.disablepreview", 0)
    _prop(sequence, "kdenlive:sequenceproperties.position", 0)
    _prop(sequence, "kdenlive:sequenceproperties.scrollPos", 0)
    _prop(sequence, "kdenlive:sequenceproperties.tracks", len(track_tractor_ids))
    _prop(sequence, "kdenlive:sequenceproperties.verticalzoom", 1)
    _prop(sequence, "kdenlive:sequenceproperties.videoTarget", video_positions[0] if video_positions else -1)
    _prop(sequence, "kdenlive:sequenceproperties.zonein", 0)
    _prop(sequence, "kdenlive:sequenceproperties.zoneout", total_frames)
    _prop(sequence, "kdenlive:sequenceproperties.zoom", 6)
    _prop(sequence, "kdenlive:sequenceproperties.groups", "[]")
    _prop(sequence, "kdenlive:sequenceproperties.guides", "[]")

    ET.SubElement(sequence, "track", {"producer": "producer_black"})
    for _, tid in track_tractor_ids:
        ET.SubElement(sequence, "track", {"producer": tid})

    for pos, (kind, _) in enumerate(track_tractor_ids, start=1):
        t = ET.SubElement(sequence, "transition", {"id": ids.next("transition")})
        _prop(t, "a_track", 0)
        _prop(t, "b_track", pos)
        if kind == "audio":
            _prop(t, "mlt_service", "mix")
            _prop(t, "kdenlive_id", "mix")
            _prop(t, "internal_added", 237)
            _prop(t, "always_active", 1)
            _prop(t, "accepts_blanks", 1)
            _prop(t, "sum", 1)
        else:
            _prop(t, "compositing", 0)
            _prop(t, "distort", 0)
            _prop(t, "rotate_center", 0)
            _prop(t, "mlt_service", "qtblend")
            _prop(t, "kdenlive_id", "qtblend")
            _prop(t, "internal_added", 237)
            _prop(t, "always_active", 1)

    mf1 = ET.SubElement(sequence, "filter", {"id": ids.next("filter")})
    _prop(mf1, "window", 75)
    _prop(mf1, "max_gain", "20dB")
    _prop(mf1, "mlt_service", "volume")
    _prop(mf1, "internal_added", 237)
    _prop(mf1, "disable", 1)
    mf2 = ET.SubElement(sequence, "filter", {"id": ids.next("filter")})
    _prop(mf2, "channel", -1)
    _prop(mf2, "mlt_service", "panner")
    _prop(mf2, "internal_added", 237)
    _prop(mf2, "start", 0.5)
    _prop(mf2, "disable", 1)

    root.append(sequence)

    # "Master" bin chains (one full-length reference per asset, for the Project Bin listing).
    bin_chain_ids: dict[str, str] = {}
    for clip in project.bin_clips:
        el, xml_id = _build_media_element(
            ids,
            path=clip.path,
            kind=clip.kind,
            in_frame=0,
            out_frame=clip.duration_frames - 1,
            fast=False,
            numeric_id=clip.numeric_id,
            control_uuid=control_uuid_for[clip.id],
        )
        root.append(el)
        bin_chain_ids[clip.id] = xml_id

    main_bin = ET.Element("playlist", {"id": "main_bin"})
    _prop(main_bin, "kdenlive:folder.-1.2", "Secuencias")
    _prop(main_bin, "kdenlive:sequenceFolder", 2)
    _prop(main_bin, "kdenlive:docproperties.audioChannels", 2)
    _prop(main_bin, "kdenlive:docproperties.binsort", 0)
    _prop(main_bin, "kdenlive:docproperties.documentid", deterministic_document_id(project.project_id))
    _prop(main_bin, "kdenlive:docproperties.enableTimelineZone", 0)
    _prop(main_bin, "kdenlive:docproperties.enableexternalproxy", 0)
    _prop(main_bin, "kdenlive:docproperties.enableproxy", 0)
    _prop(main_bin, "kdenlive:docproperties.generateimageproxy", 0)
    _prop(main_bin, "kdenlive:docproperties.generateproxy", 0)
    _prop(main_bin, "kdenlive:docproperties.kdenliveversion", KDENLIVE_VERSION)
    _prop(main_bin, "kdenlive:docproperties.profile", "custom")
    _prop(main_bin, "kdenlive:docproperties.proxyimageminsize", 2000)
    _prop(main_bin, "kdenlive:docproperties.proxyimagesize", 800)
    _prop(main_bin, "kdenlive:docproperties.proxyminsize", 1000)
    _prop(main_bin, "kdenlive:docproperties.proxyresize", 640)
    _prop(main_bin, "kdenlive:docproperties.seekOffset", 30000)
    _prop(main_bin, "kdenlive:docproperties.uuid", sequence_uuid)
    _prop(main_bin, "kdenlive:docproperties.version", DOC_VERSION)
    _prop(main_bin, "kdenlive:docproperties.opensequences", sequence_uuid)
    _prop(main_bin, "kdenlive:docproperties.activetimeline", sequence_uuid)
    _prop(main_bin, "kdenlive:documentnotesversion", 2)
    _prop(main_bin, "xml_retain", 1)

    ET.SubElement(main_bin, "entry", {"producer": sequence_uuid, "in": "0", "out": "0"})
    for clip in project.bin_clips:
        ET.SubElement(
            main_bin,
            "entry",
            {"producer": bin_chain_ids[clip.id], "in": "0", "out": str(clip.duration_frames - 1)},
        )
    root.append(main_bin)

    wrapper = ET.SubElement(root, "tractor", {"id": ids.next("tractor"), "in": "0", "out": str(total_frames - 1)})
    _prop(wrapper, "kdenlive:projectTractor", 1)
    ET.SubElement(wrapper, "track", {"in": "0", "out": str(total_frames - 1), "producer": sequence_uuid})

    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ")
    return tree


def write(project: ResolvedProject, output_path: str | Path) -> None:
    tree = generate(project)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
