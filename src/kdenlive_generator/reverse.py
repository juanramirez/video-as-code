"""Reverse converter: .kdenlive (XML/MLT) -> declarative YAML.

Goal: recognize exactly what this generator knows how to produce (video/
audio tracks, trimmed clips, fade to/from black, volume fades, per-clip
mute, forward speed changes (timewarp), simple qtblend picture-in-picture
compositing) and translate it to YAML.

Anything that's NOT recognized — other effects, non-standard transitions,
reverse-playback speed changes, titles, subtitles, extra nested sequences,
groupings, guides... — is **not discarded**: it's attached as-is (original
XML) under the `x_kdenlive_raw:` key at the end of the YAML, along with a
summary printed to the terminal, so the user can decide what to do with it
(port it by hand, ignore it, or keep using the original .kdenlive for that
part).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import probe, titles
from .i18n import msg
from .timecode import FrameRate, resolve_fps

DEFAULT_TRACK_FILTER_IDS = {"qtblend"}  # default filter the generator itself adds


@dataclass
class RawItem:
    where: str
    xml: str


@dataclass
class ImportResult:
    data: dict
    raw_items: list[RawItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _pretty(el: ET.Element) -> str:
    ET.indent(el, space="  ")
    return ET.tostring(el, encoding="unicode")


def _props(el: ET.Element) -> dict[str, str]:
    return {p.get("name"): (p.text or "") for p in el.findall("property")}


def _parse_frame_value(value: Optional[str], fps: FrameRate) -> int:
    if not value:
        return 0
    value = value.strip()
    if ":" in value:
        h, m, s = value.split(":")
        seconds = int(h) * 3600 + int(m) * 60 + float(s)
        return fps.seconds_to_frames(seconds)
    return int(round(float(value)))


class Importer:
    def __init__(self, root: ET.Element, kdenlive_path: Path):
        self.root = root
        self.path = kdenlive_path
        self.base_dir = kdenlive_path.parent
        self.by_id: dict[str, ET.Element] = {
            el.get("id"): el for el in root.iter() if el.get("id") is not None
        }
        self.raw_items: list[RawItem] = []
        self.warnings: list[str] = []
        self.bin_by_path: dict[str, str] = {}
        self.bin_entries: list[dict] = []

        profile = root.find("profile")
        if profile is None:
            raise ValueError(msg("reverse.no_profile"))
        self.width = int(profile.get("width"))
        self.height = int(profile.get("height"))
        self.colorspace = int(profile.get("colorspace", "709"))
        num = int(profile.get("frame_rate_num"))
        den = int(profile.get("frame_rate_den", "1"))
        self.fps = FrameRate(num, den)

        self.black_ids = {
            el.get("id")
            for el in list(root.findall("producer")) + list(root.findall("chain"))
            if _props(el).get("resource") == "black"
        }

    # -- locate the active sequence ------------------------------------------

    def find_main_sequence(self) -> ET.Element:
        main_bin = self.by_id.get("main_bin")
        active_uuid = None
        if main_bin is not None:
            active_uuid = _props(main_bin).get("kdenlive:docproperties.activetimeline")
        if active_uuid and active_uuid in self.by_id:
            return self.by_id[active_uuid]
        for tractor in self.root.findall("tractor"):
            if "kdenlive:uuid" in _props(tractor):
                return tractor
        raise ValueError(msg("reverse.no_sequence_found"))

    def other_sequences(self, main: ET.Element) -> list[ET.Element]:
        return [
            t
            for t in self.root.findall("tractor")
            if "kdenlive:uuid" in _props(t) and t is not main
        ]

    # -- asset registration ---------------------------------------------------

    def classify_producer(self, producer_el: ET.Element) -> Optional[tuple[str, bool, bool, Optional[dict]]]:
        """(resource, is_timewarp, is_title, title_data) for a producer/
        chain, or None if it has no usable resource (blank/black).
        `title_data` is the parsed title fields when `is_title` and this
        generator can represent the file's content, None otherwise
        (including "is_title but couldn't parse it" - the caller decides
        how to report that, since preregistering bin order and converting
        an actual timeline entry need different fallbacks)."""
        props = _props(producer_el)
        is_timewarp = props.get("mlt_service") == "timewarp"
        is_title = props.get("mlt_service") == "kdenlivetitle"
        resource = props.get("warp_resource") if is_timewarp else props.get("resource")
        if not resource or resource == "black":
            return None
        title_data = None
        if is_title:
            try:
                title_xml = Path(resource).read_text(encoding="utf-8")
            except OSError:
                title_xml = None
            title_data = titles.parse_title_xml(title_xml) if title_xml is not None else None
        return resource, is_timewarp, is_title, title_data

    def preregister_bin_order(self) -> None:
        """Seeds bin order from `main_bin`'s own entries before anything else
        registers a clip. `main_bin` lists bin clips in their original
        declaration order (see xmlgen.py's `generate`); without this, clips
        would instead get registered in whatever order the timeline tracks
        happen to reference them, which usually differs and makes the
        reimported `bin:` order — and therefore each clip's `kdenlive:id`
        on the next build — diverge from the original for no reason tied to
        the actual timeline content."""
        main_bin = self.by_id.get("main_bin")
        if main_bin is None:
            return
        for entry in main_bin.findall("entry"):
            producer_el = self.by_id.get(entry.get("producer"))
            if producer_el is None:
                continue
            classified = self.classify_producer(producer_el)
            if classified is None:
                continue
            resource, _is_timewarp, is_title, title_data = classified
            if is_title:
                if title_data is not None:
                    self.register_title_bin_clip(resource, title_data)
                # Unsupported title content: skip preregistering: the
                # actual timeline entry (convert_playlist) reports it.
            else:
                self.register_bin_clip(resource)

    def register_bin_clip(self, resource: str) -> str:
        if resource in self.bin_by_path:
            return self.bin_by_path[resource]
        p = Path(resource)
        try:
            rel = Path(resource).resolve().relative_to(self.base_dir.resolve())
            display_path = str(rel)
        except ValueError:
            display_path = resource
        base_id = p.stem or "clip"
        clip_id, n = base_id, 2
        existing_ids = {c["id"] for c in self.bin_entries}
        while clip_id in existing_ids:
            clip_id = f"{base_id}_{n}"
            n += 1
        self.bin_entries.append({"id": clip_id, "path": display_path})
        self.bin_by_path[resource] = clip_id
        return clip_id

    def register_title_bin_clip(self, resource: str, title_data: dict) -> str:
        if resource in self.bin_by_path:
            return self.bin_by_path[resource]
        base_id = Path(resource).stem or "title"
        clip_id, n = base_id, 2
        existing_ids = {c["id"] for c in self.bin_entries}
        while clip_id in existing_ids:
            clip_id = f"{base_id}_{n}"
            n += 1
        self.bin_entries.append({"id": clip_id, "type": "title", **title_data})
        self.bin_by_path[resource] = clip_id
        return clip_id

    # -- tracks -----------------------------------------------------------------

    def resolve_track_playlist(self, track_el: ET.Element, ctx: str) -> tuple[ET.Element, str]:
        """From the sequence's <track producer="..."/>, walks back to the
        playlist with the real entries and determines if it's video or audio."""
        ref = self.by_id.get(track_el.get("producer"))
        if ref is None:
            raise ValueError(msg("reverse.broken_reference", ctx=ctx, ref=track_el.get("producer")))

        if ref.tag == "playlist":
            kind = "audio" if "kdenlive:audio_track" in _props(ref) else "video"
            return ref, kind

        if ref.tag == "tractor":
            sub_tracks = ref.findall("track")
            candidates = []
            for st in sub_tracks:
                pl = self.by_id.get(st.get("producer"))
                if pl is None or pl.tag != "playlist":
                    continue
                hide = st.get("hide", "")
                kind = "audio" if hide == "video" else "video"
                has_entries = pl.find("entry") is not None
                candidates.append((pl, kind, has_entries))
            non_empty = [c for c in candidates if c[2]]
            if len(non_empty) > 1:
                for pl, _kind, _ in non_empty[1:]:
                    self.raw_items.append(RawItem(msg("reverse.secondary_playlist_with_content", ctx=ctx), _pretty(pl)))
                self.warnings.append(msg("reverse.mix_track_warning", ctx=ctx))
            if non_empty:
                return non_empty[0][0], non_empty[0][1]
            if candidates:
                return candidates[0][0], candidates[0][1]

        raise ValueError(msg("reverse.unrecognized_track_structure", ctx=ctx, tag=ref.tag))

    def convert_clip_filter(self, filter_el: ET.Element, entry_in: int, entry_out: int, ctx: str) -> dict:
        """Returns partial keys (fade_in/fade_out/position/size) to merge into
        the placement, or {} if it doesn't add anything recognized (and has
        already been dumped to raw_items)."""
        props = _props(filter_el)
        service = props.get("mlt_service")
        kid = props.get("kdenlive_id")
        f_in = _parse_frame_value(filter_el.get("in"), self.fps) if filter_el.get("in") else entry_in
        f_out = _parse_frame_value(filter_el.get("out"), self.fps) if filter_el.get("out") else entry_out
        length_s = self.fps.frames_to_seconds(f_out - f_in + 1)

        if service == "brightness" and kid == "fade_from_black":
            return {"fade_in": round(length_s, 3)}
        if service == "brightness" and kid == "fade_to_black":
            return {"fade_out": round(length_s, 3)}
        if service == "volume" and kid == "fadein":
            return {"fade_in": round(length_s, 3)}
        if service == "volume" and kid == "fadeout":
            return {"fade_out": round(length_s, 3)}
        if service == "volume" and kid == "mute":
            return {"mute": True}
        if service == "qtblend" and kid == "qtblend":
            rect = props.get("rect", "")
            keyframes = rect.split(";") if rect else []
            if len(keyframes) <= 1:
                try:
                    _, vals = rect.split("=", 1)
                    x, y, w, h, o = vals.split()
                    x, y, w, h, o = float(x), float(y), float(w), float(h), float(o)
                except (ValueError, IndexError):
                    self.raw_items.append(RawItem(msg("reverse.qtblend_rect_unreadable", ctx=ctx), _pretty(filter_el)))
                    return {}
                is_default = (x, y, w, h, o) == (0, 0, float(self.width), float(self.height), 1)
                if is_default:
                    return {}
                return {"position": [x, y], "size": [w, h]}
            self.raw_items.append(RawItem(msg("reverse.qtblend_animated_position", ctx=ctx), _pretty(filter_el)))
            return {}

        self.raw_items.append(RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service or "?", kid=kid or "?"), _pretty(filter_el)))
        return {}

    def convert_playlist(self, playlist: ET.Element, kind: str, ctx: str) -> list[dict]:
        clips = []
        cursor = 0
        for child in playlist:
            if child.tag == "property":
                continue
            if child.tag == "blank":
                cursor += _parse_frame_value(child.get("length"), self.fps)
                continue
            if child.tag != "entry":
                self.raw_items.append(RawItem(msg("reverse.unexpected_playlist_element", ctx=ctx, tag=child.tag), _pretty(child)))
                continue

            producer_id = child.get("producer")
            producer_el = self.by_id.get(producer_id)
            if producer_el is None:
                self.warnings.append(msg("reverse.entry_missing_producer", ctx=ctx, producer_id=producer_id))
                continue
            classified = self.classify_producer(producer_el)
            if classified is None:
                self.raw_items.append(RawItem(msg("reverse.clip_no_resource", ctx=ctx, tag=producer_el.tag, producer_id=producer_id), _pretty(child)))
                continue
            resource, is_timewarp, is_title, title_data = classified

            speed = None
            if is_timewarp:
                props = _props(producer_el)
                try:
                    speed = float(props.get("warp_speed", 1.0))
                except ValueError:
                    speed = None
                if not speed or speed <= 0:
                    # Reverse playback (negative speed) isn't supported -
                    # only the positive, forward-speed-change case this
                    # generator itself can produce.
                    self.raw_items.append(RawItem(msg("reverse.timewarp_unsupported", ctx=ctx), _pretty(child) + "\n" + _pretty(producer_el)))
                    cursor += _parse_frame_value(child.get("out"), self.fps) - _parse_frame_value(child.get("in"), self.fps) + 1
                    continue

            if is_title and title_data is None:
                # Richer than this generator can represent (multiple
                # items, animation, image/rect items...) or the file
                # couldn't be read - don't guess, keep the original.
                self.raw_items.append(RawItem(msg("reverse.title_unsupported", ctx=ctx), _pretty(child) + "\n" + _pretty(producer_el)))
                cursor += _parse_frame_value(child.get("out"), self.fps) - _parse_frame_value(child.get("in"), self.fps) + 1
                continue

            clip_id = self.register_title_bin_clip(resource, title_data) if is_title else self.register_bin_clip(resource)
            in_frame = _parse_frame_value(child.get("in"), self.fps)
            out_frame = _parse_frame_value(child.get("out"), self.fps)
            entry_ctx = f"{ctx}[{clip_id}@{cursor}]"

            placement = {
                "clip": clip_id,
                "start": round(self.fps.frames_to_seconds(cursor), 3),
            }
            if in_frame != 0:
                placement["in"] = round(self.fps.frames_to_seconds(in_frame), 3)
            placement["out"] = round(self.fps.frames_to_seconds(out_frame + 1), 3)
            if speed is not None:
                placement["speed"] = speed
                if props.get("warp_pitch") not in (None, "0"):
                    placement["preserve_pitch"] = True

            for filt in child.findall("filter"):
                placement.update(self.convert_clip_filter(filt, in_frame, out_frame, entry_ctx))

            clips.append(placement)
            cursor += out_frame - in_frame + 1
        return clips

    def check_unrecognized_transitions(self, sequence: ET.Element) -> None:
        """The compositing transitions this generator itself always adds
        (qtblend/mix against the black background, `a_track="0"`) are
        harmless to not reconstruct here - rebuilding from the tracks/
        clips this importer *does* recognize regenerates them identically.
        But any other sequence-level transition (non-zero `a_track`, e.g.
        a `wipe`'s `luma` transition between two content tracks) carries
        real visual information with no way to represent it in the
        low-level YAML model (there's no cross-track-transition concept
        below `scenes:`, and `scenes:` itself is never reconstructed on
        import - see `test_scenes_dissolve_produces_a_real_crossfade`'s
        sibling round-trip check). Flag it instead of silently dropping
        it: rebuilding without this would silently lose the transition
        entirely, not just the fact that it *was* a transition."""
        for t in sequence.findall("transition"):
            props = _props(t)
            if props.get("a_track", "0") != "0":
                self.raw_items.append(
                    RawItem(
                        msg("reverse.cross_track_transition_unsupported", service=props.get("mlt_service", "?")),
                        _pretty(t),
                    )
                )

    def run(self) -> ImportResult:
        self.preregister_bin_order()

        sequence = self.find_main_sequence()
        for extra in self.other_sequences(sequence):
            self.raw_items.append(RawItem(msg("reverse.extra_sequence_not_imported"), _pretty(extra)))
        self.check_unrecognized_transitions(sequence)

        title = _props(sequence).get("kdenlive:clipname", self.path.stem)
        track_els = [t for t in sequence.findall("track") if t.get("producer") not in self.black_ids]

        tracks_yaml = []
        for i, track_el in enumerate(track_els):
            ctx = f"timeline.tracks[{i}]"
            try:
                playlist, kind = self.resolve_track_playlist(track_el, ctx)
            except ValueError as exc:
                self.warnings.append(str(exc))
                self.raw_items.append(RawItem(msg("reverse.track_not_imported", ctx=ctx), _pretty(track_el)))
                continue
            clips = self.convert_playlist(playlist, kind, ctx)
            tracks_yaml.append({"type": kind, "name": f"{'V' if kind == 'video' else 'A'}{i + 1}", "clips": clips})

        data = {
            "project": {
                "id": self.path.stem,
                "title": title,
                "fps": self.fps.num if self.fps.den == 1 else round(self.fps.fps, 3),
                "width": self.width,
                "height": self.height,
                "colorspace": self.colorspace,
            },
            "bin": self.bin_entries,
            "timeline": {"tracks": tracks_yaml},
        }
        if self.raw_items:
            data["x_kdenlive_raw"] = [{"where": r.where, "xml": r.xml} for r in self.raw_items]

        return ImportResult(data=data, raw_items=self.raw_items, warnings=self.warnings)


def import_kdenlive(path: str | Path) -> ImportResult:
    path = Path(path).resolve()
    tree = ET.parse(path)
    return Importer(tree.getroot(), path).run()
