"""Reverse converter: .kdenlive (XML/MLT) -> declarative YAML.

Goal: recognize exactly what this generator knows how to produce (video/
audio tracks, trimmed clips, fade to/from black, volume fades, per-clip
mute, speed changes (timewarp, forward or reverse), title clips,
guides/markers, simple qtblend picture-in-picture compositing) and
translate it to YAML. Track-level properties (`pan:`) aren't among them
yet - this only reads per-entry filters, never a track tractor's own
filters - see BACKLOG.md.

Anything that's NOT recognized — other effects, non-standard transitions,
subtitles, extra nested sequences, groupings... — is **not discarded**:
it's attached as-is (original XML) under the `x_kdenlive_raw:` key at the
end of the YAML, along with a summary printed to the terminal, so the
user can decide what to do with it (port it by hand, ignore it, or keep
using the original .kdenlive for that part).
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from . import glfilters, gltransitions, lumas, meltpass, probe, titles, xmlgen
from .i18n import msg
from .timecode import FrameRate, resolve_fps

DEFAULT_TRACK_FILTER_IDS = {"qtblend"}  # default filter the generator itself adds

# Matches xmlgen.py's `_add_ken_burns_filter` `transition.rect` shape: two
# keyframes only, at frame 0 and the entry's own last frame, percent units
# on every field, opacity fixed at 100 - the structural shape only this
# generator's own writer produces (see `_match_ken_burns_filter`).
_KEN_BURNS_RECT_RE = re.compile(
    r"^0=(-?[\d.]+)%/(-?[\d.]+)%:(-?[\d.]+)%x(-?[\d.]+)%:100;"
    r"(\d+)=(-?[\d.]+)%/(-?[\d.]+)%:(-?[\d.]+)%x(-?[\d.]+)%:100$"
)

# Matches xmlgen.py's `_add_pillar_echo_filter` `rect` shape: a single,
# static, all-percent "X% Y% W% H%" value (no keyframes, no mixed units).
_PILLAR_ECHO_RECT_RE = re.compile(
    r"^(-?[\d.]+)%\s+(-?[\d.]+)%\s+(-?[\d.]+)%\s+(-?[\d.]+)%$"
)

# The "old film" look family (v0.8) - `_add_old_film_filters` in
# xmlgen.py writes each of these with exactly these hardcoded kdenlive
# defaults (plain on/off toggles, no per-parameter overrides), so only
# an exact match on every parameter round-trips - same "exact match"
# bar Ken Burns/reveal filters hold to.
_OLD_FILM_DEFAULTS = {
    "oldfilm": (
        {
            "delta": 14, "every": 20, "brightnessdelta_up": 20, "brightnessdelta_down": 30,
            "brightnessdelta_every": 70, "unevendevelop_up": 60, "unevendevelop_down": 20,
            "unevendevelop_duration": 70,
        },
        "old_film",
    ),
    "dust": ({"maxdiameter": 2, "maxcount": 10}, "film_dust"),
    "lines": ({"line_width": 2, "num": 5, "darker": 40, "lighter": 40}, "film_scratches"),
    "grain": ({"noise": 40, "contrast": 160, "brightness": 70}, "film_grain"),
}


@dataclass
class RawItem:
    where: str
    xml: str


@dataclass
class TrackMeta:
    """What `resolve_track_playlist` can read off a track's own tractor
    (as opposed to the playlist with the actual clip entries) - real
    values when this generator itself produced the project (or a real
    kdenlive project happens to match the same shape), `None`/`False`
    defaults otherwise, never guessed."""
    name: Optional[str] = None
    hidden: bool = False
    locked: bool = False
    audio_muted: bool = False
    pan: Optional[float] = None


@dataclass
class ImportResult:
    data: dict
    raw_items: list[RawItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # a project of nested sequences in the shape `parts:` makes (parts.py):
    # each part's file, by its path relative to the main one's folder
    files: dict[str, dict] = field(default_factory=dict)


def _pretty(el: ET.Element) -> str:
    ET.indent(el, space="  ")
    return ET.tostring(el, encoding="unicode")


def _props(el: ET.Element) -> dict[str, str]:
    return {p.get("name"): (p.text or "") for p in el.findall("property")}


def _melt_properties(kind: str, props: dict[str, str], placement_keys: tuple[str, ...]) -> Optional[dict[str, str]]:
    """A filter/transition's properties as a `melt:` entry's (meltpass.py)
    - None unless the installed melt has its service and takes every one
    of them (one that came from a newer melt, or kdenlive-only, stays raw)."""
    service = props.get("mlt_service")
    if not service or not meltpass.is_known(kind, service):
        return None
    properties = {
        k: v for k, v in props.items()
        if k not in ("mlt_service", *placement_keys) and not (k == "kdenlive_id" and v == service)
    }
    if meltpass.check(kind, service, properties, ""):
        return None
    return properties


def _has_timeremap_link(producer_el: ET.Element) -> bool:
    """Whether a `<chain>`/`<producer>` has a `timeremap` `<link>` child
    (v0.7's `speed_ramp:` mechanism - xmlgen.py's `_add_speed_ramp_link`).
    `<link>` is a sibling of `<filter>` under the chain, its own
    `mlt_service` is a nested `<property>` (not an XML attribute) -
    matches MLT's own XML writer, `serialise_link` in
    `~/dev/mlt/src/modules/xml/consumer_xml.c`."""
    return any(_props(link_el).get("mlt_service") == "timeremap" for link_el in producer_el.findall("link"))


def _parse_frame_value(value: Optional[str], fps: FrameRate) -> int:
    if not value:
        return 0
    value = value.strip()
    if ":" in value:
        h, m, s = value.split(":")
        seconds = int(h) * 3600 + int(m) * 60 + float(s)
        return fps.seconds_to_frames(seconds)
    return int(round(float(value)))


def _parse_hex_color(value: Optional[str]) -> Optional[tuple[int, int, int, int]]:
    """`0xRRGGBBAA` (dynamictext's fgcolour/bgcolour format) -> (r, g, b,
    a). Returns None for anything else rather than guessing."""
    if not value or not value.lower().startswith("0x") or len(value) != 10:
        return None
    try:
        n = int(value, 16)
    except ValueError:
        return None
    return ((n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)


class Importer:
    def __init__(self, root: ET.Element, kdenlive_path: Path):
        self.root = root
        self.path = kdenlive_path
        self.base_dir = kdenlive_path.parent
        self.absolute_paths = False  # (a part's importer: see parts_of)
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

    def bin_folder_names(self) -> dict[int, str]:
        """`kdenlive:folder.-1.<id>` properties on `main_bin` (confirmed
        against kdenlive's own source, BinPlaylist::manageBinItemInsertion)
        - id 2 is excluded, it's always the "Secuencias" folder every
        project gets (xmlgen.py's `kdenlive:sequenceFolder`), not a
        user-content folder from `folder:`."""
        main_bin = self.by_id.get("main_bin")
        if main_bin is None:
            return {}
        names: dict[int, str] = {}
        for name, value in _props(main_bin).items():
            if not name.startswith("kdenlive:folder.-1."):
                continue
            try:
                folder_id = int(name.rsplit(".", 1)[1])
            except ValueError:
                continue
            if folder_id != 2:
                names[folder_id] = value
        return names

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
        folder_names = self.bin_folder_names()
        for entry in main_bin.findall("entry"):
            producer_el = self.by_id.get(entry.get("producer"))
            if producer_el is None:
                continue
            classified = self.classify_producer(producer_el)
            if classified is None:
                continue
            resource, _is_timewarp, is_title, title_data = classified
            try:
                folder_id = int(_props(producer_el).get("kdenlive:folderid", "-1"))
            except ValueError:
                folder_id = -1
            folder = folder_names.get(folder_id)
            if is_title:
                if title_data is not None:
                    self.register_title_bin_clip(resource, title_data, folder)
                # Unsupported title content: skip preregistering: the
                # actual timeline entry (convert_playlist) reports it.
            else:
                self.register_bin_clip(resource, folder)

    def register_bin_clip(self, resource: str, folder: Optional[str] = None) -> str:
        if resource in self.bin_by_path:
            return self.bin_by_path[resource]
        p = Path(resource)
        try:
            if self.absolute_paths:  # (a part's file may be written anywhere)
                raise ValueError
            rel = Path(resource).resolve().relative_to(self.base_dir.resolve())
            display_path = str(rel)
        except ValueError:
            display_path = resource if p.is_absolute() else str((Path(self.root.get("root") or self.base_dir) / p).resolve())
        base_id = p.stem or "clip"
        clip_id, n = base_id, 2
        existing_ids = {c["id"] for c in self.bin_entries}
        while clip_id in existing_ids:
            clip_id = f"{base_id}_{n}"
            n += 1
        entry = {"id": clip_id, "path": display_path}
        if folder:
            entry["folder"] = folder
        self.bin_entries.append(entry)
        self.bin_by_path[resource] = clip_id
        return clip_id

    def register_title_bin_clip(self, resource: str, title_data: dict, folder: Optional[str] = None) -> str:
        if resource in self.bin_by_path:
            return self.bin_by_path[resource]
        base_id = Path(resource).stem or "title"
        clip_id, n = base_id, 2
        existing_ids = {c["id"] for c in self.bin_entries}
        while clip_id in existing_ids:
            clip_id = f"{base_id}_{n}"
            n += 1
        entry = {"id": clip_id, "type": "title", **title_data}
        if folder:
            entry["folder"] = folder
        self.bin_entries.append(entry)
        self.bin_by_path[resource] = clip_id
        return clip_id

    # -- tracks -----------------------------------------------------------------

    def resolve_track_playlist(self, track_el: ET.Element, ctx: str) -> tuple[ET.Element, str, TrackMeta]:
        """From the sequence's <track producer="..."/>, walks back to the
        playlist with the real entries, determines if it's video or audio,
        and reads `kdenlive:track_name`/`hide`/`kdenlive:locked_track` off
        the track's own tractor if it has one (confirmed against
        kdenlive's own source, TrackModel::TrackModel/isHidden/isMute -
        see xmlgen.py's `_build_track`) - default `TrackMeta()` otherwise
        (e.g. a bare playlist with no wrapping tractor - not a shape this
        generator itself produces, but a real project could have one)."""
        ref = self.by_id.get(track_el.get("producer"))
        if ref is None:
            raise ValueError(msg("reverse.broken_reference", ctx=ctx, ref=track_el.get("producer")))

        if ref.tag == "playlist":
            kind = "audio" if "kdenlive:audio_track" in _props(ref) else "video"
            return ref, kind, TrackMeta()

        if ref.tag == "tractor":
            track_props = _props(ref)
            try:
                hide_bits = int(track_props.get("hide", "0"))
            except ValueError:
                hide_bits = 0
            pan = None
            for filt in ref.findall("filter"):
                fprops = _props(filt)
                if fprops.get("mlt_service") == "panner" and fprops.get("disable", "1") == "0":
                    try:
                        pan = round(float(fprops.get("start", "0.5")) * 2 - 1, 3)
                    except ValueError:
                        pan = None
                    break
            meta = TrackMeta(
                name=track_props.get("kdenlive:track_name") or None,
                hidden=bool(hide_bits & 1),
                locked=track_props.get("kdenlive:locked_track", "0") not in ("0", "", None),
                audio_muted=bool(hide_bits & 2),
                pan=pan,
            )
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
                return non_empty[0][0], non_empty[0][1], meta
            if candidates:
                return candidates[0][0], candidates[0][1], meta

        raise ValueError(msg("reverse.unrecognized_track_structure", ctx=ctx, tag=ref.tag))

    def _match_reveal_filter(
        self, filter_el: ET.Element, props: dict[str, str], f_in: int, f_out: int, entry_in: int
    ) -> Optional[dict]:
        """Recognizes an `iris` (frei0r.alphaspot) / `flip`
        (frei0r.3dflippo) / `glitch` (frei0r.glitch0r) / `delaygrab`
        (frei0r.delaygrab) / `cairogradient` (frei0r.cairogradient) reveal
        filter this generator itself could have produced (xmlgen.py's
        `_add_reveal_style_filter`) - conservative, matching the exact
        keyframe strings/fixed properties that function emits (same
        "never guess" bar `wipe`/`affine` cross-track-transition
        recognition already holds to), not just "looks like the right
        service". `kind` is "in" (fade_in) or "out" (fade_out): for
        `iris`/`flip` that comes purely from which keyframe direction
        matches; the three burst styles (`glitch`/`delaygrab`/
        `cairogradient`) have no keyframe direction at all (a constant
        burst), so it's read off whether this filter's own `in` matches
        the entry's own `in` (fade_in) or its `out` matches the entry's
        own `out` (fade_out) - both filters carry an explicit `in`
        attribute (see `_add_reveal_style_filter`), so presence alone no
        longer distinguishes them."""
        service = props.get("mlt_service")
        length = f_out - f_in + 1
        if service == "frei0r.alphaspot":
            if (
                props.get("0") != "1" or props.get("1") != "0.5" or props.get("2") != "0.5"
                or props.get("6") != "0.03" or props.get("7") != "0" or props.get("8") != "1"
                or props.get("9") != "0"
            ):
                return None
            kf = props.get("3", "")
            if kf == f"0=0.02;{length - 1}=1":
                return {"style": "iris", "kind": "in"}
            if kf == f"0=1;{length - 1}=0.02":
                return {"style": "iris", "kind": "out"}
            return None
        if service == "frei0r.3dflippo":
            if props.get("10") != "1":
                return None
            kf = props.get("1", "")
            if kf == f"0=0.92;{length - 1}=0.5":
                return {"style": "flip", "kind": "in"}
            if kf == f"0=0.5;{length - 1}=0.92":
                return {"style": "flip", "kind": "out"}
            return None
        if service == "frei0r.glitch0r":
            if (
                props.get("0") != "1" or props.get("1") != "0.5" or props.get("2") != "0.8"
                or props.get("3") != "0.5"
            ):
                return None
            return {"style": "glitch", "kind": "in" if f_in == entry_in else "out"}
        if service == "frei0r.delaygrab":
            # No adjustable parameters at all (see _add_reveal_style_filter's
            # docstring) - the only thing to check is the service itself.
            return {"style": "delaygrab", "kind": "in" if f_in == entry_in else "out"}
        if service == "frei0r.cairogradient":
            if (
                props.get("0") != "gradient_linear" or props.get("1") != "#ff8800" or props.get("2") != "1"
                or props.get("3") != "#ff8800" or props.get("4") != "0" or props.get("5") != "0"
                or props.get("6") != "0" or props.get("7") != "1" or props.get("8") != "1"
            ):
                return None
            return {"style": "cairogradient", "kind": "in" if f_in == entry_in else "out"}
        return None

    def _match_ken_burns_filter(self, props: dict[str, str], f_in: int, f_out: int) -> Optional[dict]:
        """Recognizes a Ken Burns pan+zoom filter this generator itself
        could have produced (xmlgen.py's `_add_ken_burns_filter`): exactly
        two keyframes, at frame 0 and the entry's own last frame, percent
        units throughout, opacity fixed at 100, `transition.distort` off -
        the structural shape only this generator's own writer produces
        (same "not a settable field" bar `dynamictext`'s geometry/halign/
        valign hold to, see `convert_clip_filter`). The from/to rect
        *values* themselves are plain user-facing numbers and round-trip
        freely, same as `chroma_key`'s color/distance."""
        if props.get("transition.distort", "0") != "0":
            return None
        rect = props.get("transition.rect", "")
        length = f_out - f_in + 1
        m = _KEN_BURNS_RECT_RE.match(rect)
        if not m or int(m.group(5)) != length - 1:
            return None
        x0, y0, w0, h0, x1, y1, w1, h1 = (float(m.group(i)) for i in (1, 2, 3, 4, 6, 7, 8, 9))
        return {
            "ken_burns_from": [round(x0, 3), round(y0, 3), round(w0, 3), round(h0, 3)],
            "ken_burns_to": [round(x1, 3), round(y1, 3), round(w1, 3), round(h1, 3)],
        }

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
        if service == glfilters.SERVICE and props.get("shader") and (f_in, f_out) == (entry_in, entry_out):
            glsl = glfilters.from_properties(props)
            if glsl is not None:
                return {"glsl": glsl}
        if service == "avfilter.lut3d" and props.get("av.file") and (f_in, f_out) == (entry_in, entry_out):
            lut = {"lut": props["av.file"]}
            interp = props.get("av.interp", "tetrahedral")
            if interp != "tetrahedral":
                lut["lut_interp"] = interp
            return lut
        if service == "frei0r.bluescreen0r":
            # Unlike iris/flip/glitch (which encode a keyframed animation
            # this generator itself constructs, so only exact matches are
            # trustworthy), chroma_key's color/distance are plain
            # user-facing values directly representable in the schema -
            # any value round-trips, not just ones this generator would
            # have produced. Only "Invert" (param 2) isn't supported yet
            # (no invert: field exists), so that's the one thing that
            # still falls back to raw_items.
            color = props.get("0")
            if color is None or props.get("2", "0") != "0":
                self.raw_items.append(
                    RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
                )
                return {}
            result: dict = {"chroma_key": color}
            try:
                distance = float(props.get("1", "0.35"))
            except ValueError:
                distance = None
            if distance is not None and abs(distance - 0.35) > 1e-6:
                result["chroma_key_distance"] = round(distance, 3)
            return result
        if service == "dynamictext":
            # Same "plain user-facing values round-trip freely" bar as
            # chroma_key - except `geometry`/`halign`/`valign` aren't
            # settable fields in this schema at all (only the centered,
            # full-frame layout `_add_overlay_text_filter` itself always
            # writes is representable), so those specifically still need
            # an exact match.
            if (
                props.get("geometry") != "0%/0%:100%x100%:100"
                or props.get("halign") != "centre"
                or props.get("valign") != "middle"
                or props.get("argument") is None
            ):
                self.raw_items.append(
                    RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
                )
                return {}
            result = {"overlay_text": props["argument"]}
            family = props.get("family")
            if family and family != "Sans":
                result["overlay_font"] = family
            try:
                size = int(props.get("size", "48"))
            except ValueError:
                size = 48
            if size != 48:
                result["overlay_font_size"] = size
            fg = _parse_hex_color(props.get("fgcolour"))
            if fg is not None and fg != (255, 255, 255, 255):
                result["overlay_color"] = list(fg)
            bg = _parse_hex_color(props.get("bgcolour"))
            if bg is not None and bg != (0, 0, 0, 0):
                result["overlay_background"] = list(bg)
            return result
        if service == "affine":
            matched = self._match_ken_burns_filter(props, f_in, f_out)
            if matched is not None:
                return matched
            self.raw_items.append(
                RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
            )
            return {}
        if service == "pillar_echo":
            # Same "plain user-facing values round-trip freely" bar as
            # chroma_key - rect/blur are directly representable, not a
            # keyframed animation this generator itself constructs. Only
            # the single-value, all-percent shape `_add_pillar_echo_filter`
            # itself writes is recognized (a keyframed/animated rect, or
            # one mixing pixel and percent units, isn't something this
            # schema can represent) - anything else falls to raw_items.
            m = _PILLAR_ECHO_RECT_RE.match(props.get("rect", ""))
            if m is None:
                self.raw_items.append(
                    RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
                )
                return {}
            x, y, w, h = (round(float(m.group(i)), 3) for i in (1, 2, 3, 4))
            result = {"focus_rect": [x, y, w, h]}
            try:
                blur = float(props.get("blur", "4.0"))
            except ValueError:
                blur = None
            if blur is not None and abs(blur - 4.0) > 1e-6:
                result["focus_blur"] = round(blur, 3)
            return result
        if service == "freeze":
            # `frame` is a plain user-facing value (0-based from this
            # clip's own first visible frame, same as `_add_freeze_filter`
            # writes it) - any value round-trips, same bar `chroma_key`/
            # `focus_rect` already hold to.
            try:
                frame = _parse_frame_value(props.get("frame", "0"), self.fps)
            except ValueError:
                self.raw_items.append(
                    RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
                )
                return {}
            result = {"freeze_at": round(self.fps.frames_to_seconds(frame), 3)}
            if props.get("freeze_before", "0") not in ("0", "", None):
                result["freeze_before"] = True
            if props.get("freeze_after", "0") not in ("0", "", None):
                result["freeze_after"] = True
            return result
        if service == "lift_gamma_gain":
            # Plain user-facing numbers, same "any value round-trips
            # freely" bar `freeze_at`/`chroma_key` hold to (not an
            # exact-match-only shape like `old_film`'s toggles) - only
            # a group that actually differs from MLT's own neutral
            # default (lift 0,0,0 / gamma 1,1,1 / gain 1,1,1) is
            # reconstructed, mirroring how `_add_color_grade_filter`
            # only ever writes the groups present in `color_grade`.
            def _group(prefix: str, default: float) -> Optional[list[float]]:
                try:
                    r = float(props.get(f"{prefix}_r", default))
                    g = float(props.get(f"{prefix}_g", default))
                    b = float(props.get(f"{prefix}_b", default))
                except ValueError:
                    return None
                if (r, g, b) == (default, default, default):
                    return None
                return [round(r, 3), round(g, 3), round(b, 3)]

            color_grade: dict[str, list[float]] = {}
            lift = _group("lift", 0.0)
            if lift is not None:
                color_grade["lift"] = lift
            gamma = _group("gamma", 1.0)
            if gamma is not None:
                color_grade["gamma"] = gamma
            gain = _group("gain", 1.0)
            if gain is not None:
                color_grade["gain"] = gain
            if not color_grade:
                # All nine at their defaults - a no-op filter this
                # generator itself would never emit (color_grade
                # requires at least one non-empty group), but a
                # hand-built project could still have one.
                self.raw_items.append(
                    RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
                )
                return {}
            return {"color_grade": color_grade}
        if service in _OLD_FILM_DEFAULTS:
            # Same "exact match" bar as Ken Burns/reveal filters, not
            # chroma_key's "any value round-trips" one: v0.8 only ever
            # exposes these four filters as plain on/off toggles using
            # kdenlive's own defaults (no per-parameter overrides), so
            # a hand-edited project with customized parameter values
            # isn't something this schema can represent - falls to
            # raw_items rather than silently discarding the
            # customization back to kdenlive's defaults.
            defaults, yaml_key = _OLD_FILM_DEFAULTS[service]
            if all(props.get(key, str(default)) == str(default) for key, default in defaults.items()):
                return {yaml_key: True}
            self.raw_items.append(
                RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
            )
            return {}
        if service == "deshake":
            # No exposed parameters in this schema (v0.10 only ever
            # writes a bare toggle, MLT's own defaults for all eleven
            # params) - any `deshake` filter present means
            # `stabilize: true`, no exact-match check needed (unlike
            # `_OLD_FILM_DEFAULTS`, which does need one because a
            # hand-customized parameter value is possible there too but
            # isn't representable in this schema).
            return {"stabilize": True}
        if service in (
            "frei0r.alphaspot", "frei0r.3dflippo", "frei0r.glitch0r",
            "frei0r.delaygrab", "frei0r.cairogradient",
        ):
            matched = self._match_reveal_filter(filter_el, props, f_in, f_out, entry_in)
            if matched is not None:
                key = "fade_in" if matched["kind"] == "in" else "fade_out"
                return {key: round(length_s, 3), "reveal": matched["style"]}
            self.raw_items.append(
                RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service, kid=kid or "?"), _pretty(filter_el))
            )
            return {}
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
            # `position_keyframes:` (v0.7, animated PiP) - unlike
            # `ken_burns_from:`/`ken_burns_to:` (a fixed, generator-
            # constructed two-keyframe shape needing an exact match),
            # any valid multi-keyframe rect round-trips freely, same
            # "plain user-facing values" bar the single-keyframe
            # position:/size: case above already holds to - only a
            # malformed keyframe or a non-1 opacity (not something this
            # generator itself would ever write) still falls to
            # raw_items.
            parsed_pk = []
            for kf in keyframes:
                try:
                    frame_str, vals = kf.split("=", 1)
                    x, y, w, h, o = (float(v) for v in vals.split())
                except (ValueError, IndexError):
                    self.raw_items.append(RawItem(msg("reverse.qtblend_rect_unreadable", ctx=ctx), _pretty(filter_el)))
                    return {}
                if o != 1:
                    self.raw_items.append(RawItem(msg("reverse.qtblend_animated_position", ctx=ctx), _pretty(filter_el)))
                    return {}
                at_s = round(self.fps.frames_to_seconds(int(frame_str)), 3)
                parsed_pk.append({"at": at_s, "position": [x, y], "size": [w, h]})
            return {"position_keyframes": parsed_pk}

        # any other filter melt has, over the whole clip: a `melt:` entry
        if (f_in, f_out) == (entry_in, entry_out):
            properties = _melt_properties("filter", props, ())
            if properties is not None:
                return {"melt": [{service: properties or None}]}
        self.raw_items.append(RawItem(msg("reverse.unrecognized_filter", ctx=ctx, service=service or "?", kid=kid or "?"), _pretty(filter_el)))
        return {}

    def _match_crop_filters(
        self, filters: list[ET.Element]
    ) -> Optional[tuple[tuple[float, float, float, float], list[ET.Element]]]:
        """Recognizes the two-filter `crop` pattern `_add_crop_filters`
        itself writes (see its docstring in xmlgen.py for why two chained
        instances are needed): a passive `crop` filter (computes `crop.*`
        frame properties, `active` unset/0) immediately followed by an
        active one (`active=1`, does the pixel crop). Only matches the
        exact leading pair - not just any two `crop` filters found
        anywhere - since a hand-built project could plausibly use `crop`
        differently. The qtblend refill filter that follows doesn't need
        matching here: it's always the full-canvas default (crop is
        mutually exclusive with position/size), which the existing
        qtblend branch below already recognizes as a no-op and drops
        silently."""
        if len(filters) < 2:
            return None
        passive, active = filters[0], filters[1]
        passive_props = _props(passive)
        active_props = _props(active)
        if passive_props.get("mlt_service") != "crop" or active_props.get("mlt_service") != "crop":
            return None
        if passive_props.get("active", "0") != "0" or active_props.get("active") != "1":
            return None
        try:
            top = float(passive_props.get("top", 0))
            right = float(passive_props.get("right", 0))
            bottom = float(passive_props.get("bottom", 0))
            left = float(passive_props.get("left", 0))
        except ValueError:
            return None
        if min(top, right, bottom, left) < 0:
            return None
        return (round(top, 3), round(right, 3), round(bottom, 3), round(left, 3)), filters[2:]

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

            if _has_timeremap_link(producer_el):
                # A speed ramp (v0.7's `speed_ramp:`) - `classify_producer`
                # only ever reads the chain's own `resource`/`mlt_service`,
                # never a child `<link>`, so without this check a ramped
                # clip would silently come back as an ordinary unramped
                # one instead of being flagged - reconstructing the
                # original `[{at, speed}, ...]` breakpoints from a dense,
                # per-frame `time_map` isn't attempted (same "never guess"
                # bar as anything else this generator can't cleanly
                # invert), so this falls to raw_items instead.
                self.raw_items.append(RawItem(msg("reverse.speed_ramp_unsupported", ctx=ctx), _pretty(child) + "\n" + _pretty(producer_el)))
                cursor += _parse_frame_value(child.get("out"), self.fps) - _parse_frame_value(child.get("in"), self.fps) + 1
                continue

            speed = None
            if is_timewarp:
                props = _props(producer_el)
                try:
                    speed = float(props.get("warp_speed", 1.0))
                except ValueError:
                    speed = None
                if not speed:
                    # speed == 0 (or unparsable) isn't a valid timewarp -
                    # not something this generator (or kdenlive itself)
                    # can produce. Negative (reverse playback) is fine:
                    # `resource` is just "{speed}:{path}" either way.
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

            filters = child.findall("filter")
            crop_match = self._match_crop_filters(filters)
            if crop_match is not None:
                (top, right, bottom, left), filters = crop_match
                placement["crop"] = {"top": top, "right": right, "bottom": bottom, "left": left}

            for filt in filters:
                converted = self.convert_clip_filter(filt, in_frame, out_frame, entry_ctx)
                placement.setdefault("melt", []).extend(converted.pop("melt", []))
                placement.update(converted)
            if not placement.get("melt"):
                placement.pop("melt", None)

            clips.append(placement)
            cursor += out_frame - in_frame + 1
        return clips

    def _match_affine_style(self, rect: str, length_frames: int) -> Optional[str]:
        for style, template in xmlgen._AFFINE_STYLE_RECT.items():
            if rect == template.format(first=0, last=length_frames):
                return style
        return None

    def _recognize_cross_track_transition(
        self, t: ET.Element, props: dict[str, str], track_names_by_position: dict[int, str]
    ) -> Optional[dict]:
        try:
            track_a = track_names_by_position[int(props["a_track"])]
            track_b = track_names_by_position[int(props["b_track"])]
        except (KeyError, ValueError):
            return None

        start_frame = _parse_frame_value(t.get("in"), self.fps)
        end_frame = _parse_frame_value(t.get("out"), self.fps)
        start_s = round(self.fps.frames_to_seconds(start_frame), 3)
        end_s = round(self.fps.frames_to_seconds(end_frame + 1), 3)
        service = props.get("mlt_service")

        if service == "luma":
            luma_name = lumas.name_from_resource(props.get("resource", ""))
            try:
                softness = float(props.get("softness", 0))
            except ValueError:
                luma_name = None
            if luma_name is not None:
                wipe = {"type": "wipe", "track_a": track_a, "track_b": track_b, "start": start_s, "end": end_s, "luma": luma_name}
                if softness:
                    wipe["softness"] = softness
                if props.get("invert", "0") not in ("", "0"):  # see xmlgen.py's wipe
                    wipe["reverse"] = True
                return wipe

        if service == "affine":
            style = self._match_affine_style(props.get("rect", ""), end_frame - start_frame)
            if style is not None:
                return {"type": "affine", "track_a": track_a, "track_b": track_b, "start": start_s, "end": end_s, "style": style}

        if service == gltransitions.SERVICE:
            gl = gltransitions.from_properties(props)
            if gl is not None:
                name, params = gl
                transition = {"type": "gl", "track_a": track_a, "track_b": track_b, "start": start_s, "end": end_s, "gl": name}
                if params:
                    transition["gl_params"] = params
                return transition

        # any other (a luma map or affine motion of someone else's
        # included): as `melt/<service>`, if melt has it
        properties = _melt_properties("transition", props, ("a_track", "b_track"))
        if properties is None:
            return None
        transition = {"type": meltpass.PREFIX + service, "track_a": track_a, "track_b": track_b, "start": start_s, "end": end_s}
        if properties:
            transition["properties"] = properties
        return transition

    def convert_cross_track_transitions(
        self, sequence: ET.Element, track_names_by_position: dict[int, str]
    ) -> list[dict]:
        """The compositing transitions this generator itself always adds
        (qtblend/mix against the black background, `a_track="0"`) are
        harmless to not reconstruct here - rebuilding from the tracks/
        clips this importer *does* recognize regenerates them identically.
        Any other sequence-level transition (non-zero `a_track`) carries
        real visual information; a `wipe`'s `luma` transition or an
        `affine` transition between two content tracks *this generator
        itself could have produced* is translated back into a top-level
        `transitions:` entry (the low-level, `scenes:`-free way to
        declare one - schema.py's `_parse_transition`) instead of being
        silently lost. Reconstructing the original `scenes:` sugar (clip
        list, pacing) isn't attempted - only the transition itself,
        referencing the already-recognized track names. Anything that
        doesn't match what this generator itself would produce (a luma
        resource that isn't one of this project's own lumas, an affine
        `rect` that doesn't match the zoom/slide keyframe shape xmlgen.py
        emits, any other service) comes back as a `melt/<service>` entry
        with its properties as they are (meltpass.py) when the installed
        melt has it and takes them all; otherwise (that, or an
        out-of-range track position) it's flagged into `raw_items`
        instead of guessed."""
        transitions_yaml: list[dict] = []
        for t in sequence.findall("transition"):
            props = _props(t)
            if props.get("a_track", "0") == "0":
                continue
            recognized = self._recognize_cross_track_transition(t, props, track_names_by_position)
            if recognized is not None:
                transitions_yaml.append(recognized)
            else:
                self.raw_items.append(
                    RawItem(
                        msg("reverse.cross_track_transition_unsupported", service=props.get("mlt_service", "?")),
                        _pretty(t),
                    )
                )
        return transitions_yaml

    def parse_guides(self, sequence: ET.Element) -> list[dict]:
        """`kdenlive:sequenceproperties.guides` is a JSON array of
        `{"pos": frame, "comment": str, "type": category}` (confirmed
        against kdenlive's own source, MarkerListModel::toJson - same
        format xmlgen.py now writes). Malformed JSON (a hand-edited or
        pre-this-format project) is reported as a raw item rather than
        crashing the whole import."""
        raw = _props(sequence).get("kdenlive:sequenceproperties.guides", "")
        if not raw.strip():
            return []
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            entries = None
        if not isinstance(entries, list):
            self.raw_items.append(RawItem(msg("reverse.guides_unreadable"), raw))
            return []
        guides = []
        for entry in entries:
            if not isinstance(entry, dict) or "pos" not in entry:
                self.raw_items.append(RawItem(msg("reverse.guides_unreadable"), json.dumps(entry)))
                continue
            guide = {"at": round(self.fps.frames_to_seconds(int(entry["pos"])), 3), "text": entry.get("comment", "Marker")}
            category = int(entry.get("type", 0))
            if category:
                guide["category"] = category
            guides.append(guide)
        return guides

    def run(self) -> ImportResult:
        self.preregister_bin_order()

        sequence = self.find_main_sequence()
        title = _props(sequence).get("kdenlive:clipname", self.path.stem)
        project_yaml = {
            "id": self.path.stem,
            "title": title,
            "fps": self.fps.num if self.fps.den == 1 else round(self.fps.fps, 3),
            "width": self.width,
            "height": self.height,
            "colorspace": self.colorspace,
        }
        main_bin = self.by_id.get("main_bin")
        notes = _props(main_bin).get("kdenlive:documentnotes") if main_bin is not None else None
        if notes:
            project_yaml["notes"] = notes

        files: dict[str, dict] = {}
        parts_yaml = self.parts_of(sequence, "", files)
        if parts_yaml is not None:  # a project of parts (parts.py)
            return ImportResult(data={"project": project_yaml, "parts": parts_yaml}, warnings=self.warnings, files=files)

        for extra in self.other_sequences(sequence):
            self.raw_items.append(RawItem(msg("reverse.extra_sequence_not_imported"), _pretty(extra)))
        data = {"project": project_yaml, **self.sequence_yaml(sequence)}
        if self.raw_items:
            data["x_kdenlive_raw"] = [{"where": r.where, "xml": r.xml} for r in self.raw_items]
        return ImportResult(data=data, raw_items=self.raw_items, warnings=self.warnings)

    def sequence_yaml(self, sequence: ET.Element) -> dict:
        """One sequence's bin:, timeline:, guides: and transitions:."""
        guides = self.parse_guides(sequence)
        track_els = [t for t in sequence.findall("track") if t.get("producer") not in self.black_ids]

        tracks_yaml = []
        track_names_by_position: dict[int, str] = {}
        for i, track_el in enumerate(track_els):
            ctx = f"timeline.tracks[{i}]"
            try:
                playlist, kind, meta = self.resolve_track_playlist(track_el, ctx)
            except ValueError as exc:
                self.warnings.append(str(exc))
                self.raw_items.append(RawItem(msg("reverse.track_not_imported", ctx=ctx), _pretty(track_el)))
                continue
            clips = self.convert_playlist(playlist, kind, ctx)
            name = meta.name or f"{'V' if kind == 'video' else 'A'}{i + 1}"
            track_yaml = {"type": kind, "name": name}
            if meta.hidden:
                track_yaml["hidden"] = True
            if meta.locked:
                track_yaml["locked"] = True
            if meta.audio_muted:
                track_yaml["audio_muted"] = True
            if meta.pan is not None:
                track_yaml["pan"] = meta.pan
            track_yaml["clips"] = clips
            tracks_yaml.append(track_yaml)
            track_names_by_position[i + 1] = name

        transitions_yaml = self.convert_cross_track_transitions(sequence, track_names_by_position)

        data = {"bin": self.bin_entries, "timeline": {"tracks": tracks_yaml}}
        if guides:
            data["guides"] = guides
        if transitions_yaml:
            data["transitions"] = transitions_yaml
        return data

    # -- a project of parts (parts.py) ----------------------------------------

    def _is_sequence(self, el: Optional[ET.Element]) -> bool:
        return el is not None and el.tag == "tractor" and "kdenlive:uuid" in _props(el)

    def parts_of(self, sequence: ET.Element, own_file: str, files: dict[str, dict]) -> Optional[list]:
        """`sequence` as a `parts:` list - each part's file added to
        `files` - if it has the shape parts.py gives a file with parts:
        its tracks hold only whole other sequences, each once, one after
        the other or overlapping by a transition this importer knows
        (a wipe, affine, gl or melt/ transition between the two tracks, or
        a fade - a dissolve - or reveal on one of them). None otherwise."""
        placed = []  # (start, end, track position, entry, sequence)
        for pos, track_el in enumerate((t for t in sequence.findall("track") if t.get("producer") not in self.black_ids), start=1):
            try:
                playlist, _kind, _meta = self.resolve_track_playlist(track_el, f"track {pos}")
            except ValueError:
                return None
            cursor = 0
            for child in playlist:
                if child.tag == "blank":
                    cursor += int(child.get("length"))
                elif child.tag == "entry":
                    nested = self.by_id.get(child.get("producer"))
                    if not self._is_sequence(nested):
                        return None
                    first = _parse_frame_value(child.get("in", "0"), self.fps)
                    last = _parse_frame_value(child.get("out"), self.fps)
                    whole = _parse_frame_value(nested.get("out"), self.fps)
                    if first != 0 or last != whole:
                        return None  # (a part is placed whole)
                    placed.append((cursor, cursor + last, pos, child, nested))
                    cursor += last + 1
        if not placed or len({id(p[4]) for p in placed}) != len(placed):
            return None
        placed.sort(key=lambda p: p[0])
        transitions = [t for t in sequence.findall("transition") if _props(t).get("a_track", "0") != "0"]
        names = {pos: f"T{pos}" for pos in range(1, 64)}
        entries = []
        for i, (start, end, pos, entry_el, nested) in enumerate(placed):
            name = _props(nested).get("kdenlive:clipname") or nested.get("id").strip("{}")
            file = f"{name}.yaml"
            here = str(PurePosixPath(own_file).parent) if own_file else "."
            entry: dict = {"file": os.path.relpath(file, here).replace(os.sep, "/")}
            if i > 0:
                prev_start, prev_end, prev_pos, prev_el, _ = placed[i - 1]
                if start <= prev_end:  # overlapping: a transition into this part
                    overlap = round(self.fps.frames_to_seconds(prev_end - start + 1), 3)
                    keys = self._part_transition(transitions, names, start, prev_end, prev_el, entry_el, prev_end, start)
                    if keys is None:
                        return None
                    entry.update(keys)
                    entry["transition_duration"] = overlap
                elif start != prev_end + 1:
                    return None  # (a gap: not parts in turn)
            sub = Importer(self.root, self.path)
            sub.absolute_paths = True
            nested_parts = sub.parts_of(nested, file, files)
            if nested_parts is not None:
                files[file] = {"parts": nested_parts}
            else:
                sub.preregister_bin_order()
                part = sub.sequence_yaml(nested)
                used = {c.get("clip") for t in part["timeline"]["tracks"] for c in t["clips"]}
                part["bin"] = [b for b in part["bin"] if b.get("id") in used]
                if sub.raw_items:
                    part["x_kdenlive_raw"] = [{"where": r.where, "xml": r.xml} for r in sub.raw_items]
                self.warnings += sub.warnings
                files[file] = part
            entries.append(entry if len(entry) > 1 else entry["file"])
        return entries

    def _part_transition(self, transitions, names, start, end, prev_el, entry_el, prev_end, this_start) -> Optional[dict]:
        """A parts: entry's transition keys for the overlap start..end."""
        for t in transitions:
            if t.get("in") is None:
                continue
            if _parse_frame_value(t.get("in"), self.fps) == start and _parse_frame_value(t.get("out"), self.fps) == end:
                found = self._recognize_cross_track_transition(t, _props(t), names)
                if found is None:
                    return None
                kind = found["type"]
                if kind == "wipe":
                    keys = {"transition": "wipe", "luma": found["luma"]}
                    if found.get("softness"):
                        keys["luma_softness"] = found["softness"]
                    if found.get("reverse"):
                        keys["luma_reverse"] = True
                    return keys
                if kind == "affine":
                    return {"transition": "affine", "style": found["style"]}
                if kind == "gl":
                    return {"transition": "gl", "gl": found["gl"], **({"gl_params": found["gl_params"]} if found.get("gl_params") else {})}
                return {"transition": kind, **({"transition_properties": found["properties"]} if found.get("properties") else {})}
        # no transition element: a fade (dissolve) or reveal on one of them
        for el in (entry_el, prev_el):
            for filt in el.findall("filter"):
                got = self.convert_clip_filter(filt, 0, 1 << 30, "parts")
                if "reveal" in got:
                    return {"transition": got["reveal"]}
                if "fade_in" in got or "fade_out" in got:
                    return {"transition": "dissolve"}
        return None


def import_kdenlive(path: str | Path) -> ImportResult:
    path = Path(path).resolve()
    tree = ET.parse(path)
    return Importer(tree.getroot(), path).run()
