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

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from . import lumas
from .ids import deterministic_document_id, deterministic_uuid
from .models import ResolvedAffineTransition, ResolvedPlacement, ResolvedProject, ResolvedTrack

# Keyframed `rect` for each `transition: affine` style (scenes.py), 0-relative
# to the transition's own `in` (confirmed empirically, same convention the
# fade filters below rely on: a keyframe index is relative to the owning
# element's own `in`, not an absolute timeline frame). Format is MLT's own
# "X/Y:WxH:opacity" (percentages throughout - mixing absolute and percentage
# fields in the same rect is rejected/misbehaves, confirmed empirically).
_AFFINE_STYLE_RECT = {
    "zoom": "{first}=50%/50%:1%x1%:100;{last}=0%/0%:100%x100%:100",
    "slide": "{first}=100%/0%:100%x100%:100;{last}=0%/0%:100%x100%:100",
}

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


def _service_for(kind: str) -> str:
    if kind == "image":
        return "qimage"
    if kind == "title":
        return "kdenlivetitle"
    # Real kdenlive uses `avformat-novalidate` (skips re-probing an
    # already-validated bin clip) for timeline-track producer chains, as
    # a startup-time optimization for its own editor. Deliberately not
    # replicated here: verified by real render (two independent MLT
    # builds, and reproduced with a fully synthetic ffmpeg-generated
    # fixture, not just real phone footage) that `avformat-novalidate`
    # silently decodes a flat/blank frame whenever the video stream isn't
    # the *first* stream in the container (e.g. any file muxed with its
    # audio track before its video track) - plain `avformat` decodes the
    # exact same file correctly regardless of stream order. Common real
    # phone footage hits this because the camera app's own container
    # layout doesn't put video first (confirmed on a real Pixel 8 clip,
    # which additionally happens to be 10-bit HDR - that HDR-ness turned
    # out to be a red herring, not the actual trigger; a plain SDR
    # audio-then-video file reproduces the exact same blank frame). See
    # `tests/test_melt_validation.py::test_video_stream_not_first_still_decodes`.
    # Correctness for real footage wins over matching an editor-load perf
    # optimization that silently breaks on a common container layout.
    return "avformat"


def _resource_for(placement_kind: str, path: Path) -> str:
    return str(path)


def _build_media_element(
    ids: _IdCounters,
    *,
    path: Path,
    kind: str,
    in_frame: int,
    out_frame: int,
    numeric_id: int,
    control_uuid: str,
    speed: Optional[float] = None,
    preserve_pitch: bool = False,
    speed_ramp: Optional[list[tuple[int, float]]] = None,
    speed_ramp_image_mode: str = "nearest",
    fps: Optional[float] = None,
    folder_id: int = -1,
) -> ET.Element:
    if speed is not None:
        # A real `timewarp` producer (not a `chain`) - matches what
        # kdenlive itself generates for a sped-up/slowed-down clip
        # (confirmed against a real user project, not modeled by
        # analogy): `resource` is "{speed}:{path}", not just the path.
        # `in`/`out` here are on the *warped* timeline (already computed
        # by schema.py), not the original file's.
        tag = "producer"
        xml_id = ids.next("producer")
        el = ET.Element(tag, {"id": xml_id, "in": str(in_frame), "out": str(out_frame)})
        _prop(el, "length", out_frame + 1)
        _prop(el, "eof", "pause")
        _prop(el, "resource", f"{speed:.6f}:{path}")
        _prop(el, "warp_speed", speed)
        _prop(el, "warp_resource", str(path))
        _prop(el, "warp_pitch", int(preserve_pitch))
        _prop(el, "mlt_service", "timewarp")
        _prop(el, "kdenlive:originalurl", str(path))
        _prop(el, "kdenlive:folderid", folder_id)
        _prop(el, "kdenlive:id", numeric_id)
        _prop(el, "kdenlive:control_uuid", control_uuid)
        return el, xml_id

    if kind == "title":
        # Real kdenlive titles are a plain <producer> too, not <chain>
        # (confirmed against a real project with an actual title clip -
        # `resource` points at the separate .kdenlivetitle file titles.py
        # generates, not the media file conventions below).
        tag = "producer"
        xml_id = ids.next("producer")
        el = ET.Element(tag, {"id": xml_id, "in": str(in_frame), "out": str(out_frame)})
        _prop(el, "length", out_frame + 1)
        _prop(el, "eof", "pause")
        _prop(el, "resource", _resource_for(kind, path))
        _prop(el, "aspect_ratio", 1)
        _prop(el, "seekable", 1)
        _prop(el, "progressive", 1)
        _prop(el, "mlt_service", "kdenlivetitle")
        _prop(el, "kdenlive:folderid", folder_id)
        _prop(el, "kdenlive:id", numeric_id)
        _prop(el, "kdenlive:control_uuid", control_uuid)
        return el, xml_id

    tag = "chain"
    xml_id = ids.next("chain")
    el = ET.Element(tag, {"id": xml_id, "in": str(in_frame), "out": str(out_frame)})
    service = _service_for(kind)
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
    _prop(el, "kdenlive:folderid", folder_id)
    _prop(el, "kdenlive:id", numeric_id)
    _prop(el, "kdenlive:control_uuid", control_uuid)
    if speed_ramp is not None:
        assert fps is not None, "speed_ramp requires fps"
        _add_speed_ramp_link(el, speed_ramp, speed_ramp_image_mode, preserve_pitch, out_frame + 1, fps)
    return el, xml_id


def _speed_ramp_time_map(ramp: list[tuple[int, float]], out_frames: int, fps: float) -> str:
    """Dense (one keyframe per output frame) MLT anim string for
    `timeremap`'s `time_map` property, given piecewise-constant-speed
    segments (`ramp`: `[(at_frame, speed), ...]`, sorted, first at frame
    0 - each entry's speed holds flat until the next entry's own frame,
    matching `ClipPlacementSpec.speed_ramp`'s "several flat segments in
    sequence" model). Keys are output frames; **values are seconds, not
    frames** - `time_map` reads are wrapped in kdenlive's own
    `GenTime(double seconds)` constructor
    (`~/dev/kdenlive/src/utils/gentime.h`), confirmed the hard way: an
    earlier attempt used frame numbers for both and silently produced a
    wildly-wrong, clamped mapping instead of an error.

    Dense, not just one keyframe per ramp breakpoint: a sparse 2-keyframe
    linear map (start and end only) was verified by real render to
    under-shoot the target - the render never reached the intended final
    source position, landing roughly 75% of the way there despite
    mathematically-correct endpoints. Root cause not fully understood,
    but sampling every output frame (rather than relying on MLT's own
    interpolation across a long gap) was verified reliable instead - see
    ROADMAP.md."""
    segment_start_source_s = [0.0]
    for i in range(len(ramp) - 1):
        at_frame, speed = ramp[i]
        next_at_frame = ramp[i + 1][0]
        segment_start_source_s.append(segment_start_source_s[-1] + (next_at_frame - at_frame) / fps * speed)

    parts = []
    seg = 0
    for out_frame in range(out_frames):
        while seg + 1 < len(ramp) and out_frame >= ramp[seg + 1][0]:
            seg += 1
        at_frame, speed = ramp[seg]
        source_s = segment_start_source_s[seg] + (out_frame - at_frame) / fps * speed
        parts.append(f"{out_frame}={source_s:.6f}")
    return ";".join(parts)


def _add_speed_ramp_link(
    chain_el: ET.Element,
    ramp: list[tuple[int, float]],
    image_mode: str,
    preserve_pitch: bool,
    out_frames: int,
    fps: float,
) -> None:
    """Attaches a `timeremap` **link** (not a filter) to a `<chain>` -
    matches kdenlive's own construction exactly
    (`~/dev/kdenlive/src/bin/projectclip.cpp`: `Mlt::Link
    link("timeremap"); ... chain->attach(link);`), and its real XML
    serialization shape (`id` attribute + nested `<property>` children,
    confirmed against MLT's own XML writer,
    `~/dev/mlt/src/modules/xml/consumer_xml.c`'s `serialise_link` -
    *not* `mlt_service` as an XML attribute directly on `<link>`, which
    also happens to parse, but doesn't match what kdenlive itself
    writes)."""
    link_el = ET.SubElement(chain_el, "link", {"id": "link0"})
    _prop(link_el, "mlt_service", "timeremap")
    _prop(link_el, "time_map", _speed_ramp_time_map(ramp, out_frames, fps))
    _prop(link_el, "pitch", int(preserve_pitch))
    _prop(link_el, "image_mode", image_mode)


def _add_crop_filters(ids: _IdCounters, entry: ET.Element, crop: tuple[float, float, float, float]) -> None:
    """Edge crop via MLT's own `crop` filter, matching kdenlive's own
    "Edge Crop" effect exactly (`~/dev/kdenlive/data/effects/crop.xml`) -
    but deliberately NOT the "obvious" single-filter shape, for two
    reasons both confirmed empirically rather than assumed:

    1. `crop` genuinely needs **two** filter instances on the same clip:
       one with `top`/`right`/`bottom`/`left` and `active` left at its
       default `0` (this one only *computes* the crop and stores it as
       `crop.top`/`crop.right`/`crop.bottom`/`crop.left`/
       `crop.original_width`/`crop.original_height` properties on the
       FRAME, not the filter - confirmed by reading MLT's own filter
       source, `~/dev/mlt/src/modules/core/filter_crop.c`), and a second,
       bare instance with only `active: 1` (which reads those frame
       properties back and performs the actual pixel crop). A single
       `active: 1` instance alone produces zero measurable effect - it
       reads `crop.*` off the frame, which nothing has set yet, so every
       margin evaluates to 0. Setting `crop.*` directly as plain
       properties on the chain/entry instead of via the passive filter
       does NOT work either (frame properties don't inherit arbitrary
       custom producer properties) - checked directly, not assumed.

    2. Cropping shrinks the frame's own reported dimensions (e.g. a
       320x240 clip cropped by 30px top+bottom becomes 320x180
       internally) - encoding that non-profile-sized frame directly
       reliably **segfaults** the from-source `melt` build
       (7.41.0-dev) this project's test suite prefers, in the AVFORMAT
       CONSUMER's own `libswscale` (confirmed with `gdb`: the crash is
       in `sws_scale`, not in the crop filter itself - a real
       environment/library issue, not a bug in this project's own
       output). A `qtblend` pass immediately after crop, rescaling the
       cropped remainder back to fill the whole canvas, avoids ever
       handing that consumer a non-profile-sized frame - this project's
       existing per-track `qtblend` default filter *is* that pass
       (emitted right after this function returns), so no extra filter
       is added here beyond the two crop instances themselves.

       This means the *visible* result is "crop and zoom to fill,"
       not kdenlive's own unscaled "crop and letterbox" - confirmed
       correct and stable via real render on both the from-source dev
       build and the system's stable `melt` (7.32.0): a checkerboard's
       vertical square period visibly grows after a top+bottom crop
       (cropped-then-rescaled), not just clipped - see
       `tests/test_melt_validation.py::test_crop_produces_a_real_crop_and_fill`."""
    top, right, bottom, left = crop
    passive = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(passive, "mlt_service", "crop")
    _prop(passive, "top", f"{top:g}")
    _prop(passive, "right", f"{right:g}")
    _prop(passive, "bottom", f"{bottom:g}")
    _prop(passive, "left", f"{left:g}")
    _prop(passive, "kdenlive:collapsed", 0)
    active = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(active, "mlt_service", "crop")
    _prop(active, "active", 1)
    _prop(active, "kdenlive:collapsed", 0)


def _add_video_default_filter(
    ids: _IdCounters,
    entry: ET.Element,
    width: int,
    height: int,
    rect,
    position_keyframes: Optional[list[tuple[int, float, float, float, float]]] = None,
) -> None:
    """Animated PiP (`position_keyframes`) reuses this same per-track
    `qtblend` filter every video track already gets, just with more than
    one `rect` keyframe - verified by real render before wiring in: a
    PiP box moved cleanly from one corner to the other over a clip's
    duration with a real multi-keyframe `rect` string (`"0=... ;
    N=..."`), the same pixel-absolute "X Y W H opacity" format the
    static single-keyframe case already uses, just repeated per
    keyframe - not assumed to "just work" from the static case alone."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "rotate_center", 1)
    _prop(f, "mlt_service", "qtblend")
    _prop(f, "kdenlive_id", "qtblend")
    if position_keyframes is not None:
        rect_str = ";".join(f"{at_frame}={x:g} {y:g} {w:g} {h:g} 1" for at_frame, x, y, w, h in position_keyframes)
    else:
        if rect is not None:
            x, y, w, h = rect
        else:
            x, y, w, h = 0, 0, width, height
        rect_str = f"0={x:g} {y:g} {w:g} {h:g} 1"
    _prop(f, "rect", rect_str)
    _prop(f, "rotation", "0=0")
    _prop(f, "compositing", 0)
    _prop(f, "distort", 1)
    _prop(f, "kdenlive:collapsed", 0)


def _add_video_fade_filters(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """The `level` property carries the animated brightness ramp; `alpha`
    stays fixed at -1 ("follows level", per MLT's own filter_brightness.yml
    docs) rather than being keyframed itself. Confirmed by rendering and
    measuring actual pixel brightness: keyframing `alpha` with `level`
    fixed at 1 (the original version of this function) produces no visible
    change at all - `level` is the property that actually scales pixel
    values, independent of any alpha/compositing behavior."""
    p_in, p_out = placement.in_frame, placement.out_frame
    if placement.fade_in_frames > 0:
        L = placement.fade_in_frames
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "out": str(p_in + L - 1)})
        _prop(f, "start", 1)
        _prop(f, "level", f"0=0;{L - 1}=1")
        _prop(f, "mlt_service", "brightness")
        _prop(f, "kdenlive_id", "fade_from_black")
        _prop(f, "alpha", -1)
        _prop(f, "kdenlive:collapsed", 0)
    if placement.fade_out_frames > 0:
        L = placement.fade_out_frames
        f = ET.SubElement(
            entry,
            "filter",
            {"id": ids.next("filter"), "in": str(p_out - L + 1), "out": str(p_out)},
        )
        _prop(f, "start", 1)
        _prop(f, "level", f"0=1;{L - 1}=0")
        _prop(f, "mlt_service", "brightness")
        _prop(f, "kdenlive_id", "fade_to_black")
        _prop(f, "alpha", -1)
        _prop(f, "kdenlive:collapsed", 0)


def _add_reveal_style_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """`iris` (frei0r.alphaspot) / `flip` (frei0r.3dflippo): single-clip
    filters that write real alpha transparency, composited via the
    `qtblend` this project already emits per video track - the same
    mechanism `_add_video_fade_filters`'s brightness alpha uses, just a
    different filter (and a genuinely different reveal shape) instead of
    a plain fade. Both verified with real melt renders, not modeled from
    documentation alone: `alphaspot`'s "Shape" parameter needs to be
    close to 1 for an actual circle - values below ~0.3 render as a
    plain rectangle despite silently accepting any 0..1 float; `3dflippo`'s
    "Fill with image or black" (param 10) has to be 1 for the
    rotated-away area to be real transparency (revealing the track
    below) - 0 doesn't give transparency at all, it shows that same
    clip's own unrotated pixels underneath instead, and against a plain
    black background the two look identical without a real render to
    tell them apart (see ROADMAP.md's v0.4 entry for how this was
    checked).

    `glitch` (frei0r.glitch0r), `delaygrab` (frei0r.delaygrab), and
    `cairogradient` (frei0r.cairogradient) are a different case entirely -
    none writes any alpha at all (confirmed by real render for all
    three: with a colored clip on the track below, it never shows
    through - unlike iris/flip, these are opaque overwrites of the
    clip's own pixels), so there's no "from/to" reveal direction the way
    iris/flip's size or rotation animate: just a constant-parameter burst
    for whichever window (fade_in and/or fade_out) is set.
    - `glitch`: verified by real render to produce clearly visible
      block-shifting distortion (`avfilter.rgbashift`, its would-be
      chromatic-aberration companion, produced no visible effect in this
      build and isn't used - see scenes.py's `BURST_TRANSITIONS`
      comment).
    - `delaygrab`: verified by real render - a moving element crossing
      the filter's (fixed-position, default-sized) sample region leaves
      a stale/delayed ghost there for a brief window before the delay
      buffer catches back up to live content (checked by diffing against
      an unfiltered baseline: a strong, localized difference early,
      fading to near-zero once the moving element has passed through).
      Exposes no adjustable parameters at all - neither does kdenlive's
      own effect definition for it (`frei0r_delaygrab.xml`), and
      `melt -query filter=frei0r.delaygrab` doesn't list an
      introspectable parameter set either - so this is used with its
      plugin defaults, not modeled by analogy.
    - `cairogradient`: verified by real render - a diagonal color-wash
      gradient blended directly onto the clip's own pixels (checked with
      a green clip on top of a blue background: the composited result
      never shows any blue, confirming the gradient tints the top clip
      itself rather than creating a reveal)."""
    p_in, p_out = placement.in_frame, placement.out_frame
    style = placement.reveal_style

    if style in ("glitch", "delaygrab", "cairogradient"):
        def burst_props(f: ET.Element) -> None:
            if style == "glitch":
                _prop(f, "mlt_service", "frei0r.glitch0r")
                _prop(f, "0", 1)  # Glitch frequency: constant for the whole burst window
                _prop(f, "1", 0.5)  # Block height
                _prop(f, "2", 0.8)  # Shift intensity
                _prop(f, "3", 0.5)  # Color glitching intensity
            elif style == "delaygrab":
                _prop(f, "mlt_service", "frei0r.delaygrab")
                # No properties beyond mlt_service: see the docstring
                # above - this filter has no introspectable/adjustable
                # parameters, used with its plugin defaults.
            else:  # cairogradient
                _prop(f, "mlt_service", "frei0r.cairogradient")
                _prop(f, "0", "gradient_linear")  # Pattern
                _prop(f, "1", "#ff8800")  # Start color: warm light-leak orange
                _prop(f, "2", 1)  # Start opacity
                _prop(f, "3", "#ff8800")  # End color
                _prop(f, "4", 0)  # End opacity: fades to transparent -> a wash, not a flat tint
                _prop(f, "5", 0)  # Start X
                _prop(f, "6", 0)  # Start Y
                _prop(f, "7", 1)  # End X
                _prop(f, "8", 1)  # End Y: diagonal top-left -> bottom-right gradient

        if placement.fade_in_frames > 0:
            L = placement.fade_in_frames
            f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "out": str(p_in + L - 1)})
            burst_props(f)
            _prop(f, "kdenlive:collapsed", 0)
        if placement.fade_out_frames > 0:
            L = placement.fade_out_frames
            f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "in": str(p_out - L + 1), "out": str(p_out)})
            burst_props(f)
            _prop(f, "kdenlive:collapsed", 0)
        return

    service = "frei0r.alphaspot" if style == "iris" else "frei0r.3dflippo"

    def iris_props(f: ET.Element, size_from: float, size_to: float, length: int) -> None:
        _prop(f, "mlt_service", service)
        _prop(f, "0", 1)  # Shape: circle/ellipse
        _prop(f, "1", 0.5)  # Position X: center
        _prop(f, "2", 0.5)  # Position Y: center
        _prop(f, "3", f"0={size_from:g};{length - 1}={size_to:g}")  # Size X
        _prop(f, "4", f"0={size_from:g};{length - 1}={size_to:g}")  # Size Y
        _prop(f, "6", 0.03)  # Transition width: soft edge
        _prop(f, "7", 0)  # Min
        _prop(f, "8", 1)  # Max
        _prop(f, "9", 0)  # Operation

    def flip_props(f: ET.Element, rotate_from: float, rotate_to: float, length: int) -> None:
        _prop(f, "mlt_service", service)
        _prop(f, "1", f"0={rotate_from:g};{length - 1}={rotate_to:g}")  # Y axis rotation
        _prop(f, "10", 1)  # Fill with image or black: 1 = real transparency here

    if placement.fade_in_frames > 0:
        L = placement.fade_in_frames
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "out": str(p_in + L - 1)})
        if style == "iris":
            iris_props(f, 0.02, 1.0, L)
        else:
            flip_props(f, 0.92, 0.5, L)
        _prop(f, "kdenlive:collapsed", 0)
    if placement.fade_out_frames > 0:
        L = placement.fade_out_frames
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter"), "in": str(p_out - L + 1), "out": str(p_out)})
        if style == "iris":
            iris_props(f, 1.0, 0.02, L)
        else:
            flip_props(f, 0.5, 0.92, L)
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
        _prop(f, "gain", placement.fade_in_level)
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


def _add_chroma_key_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Green/blue-screen compositing via `frei0r.bluescreen0r` - this is
    kdenlive's own mechanism for it (confirmed: kdenlive's UI ships this
    exact filter). Deliberately not `avfilter.chromakey`: that
    FFmpeg-bridged filter produced a "Cannot set src colorspace" warning
    and no visible keying effect at all on this project's real melt
    build (same failure `avfilter.rgbashift` had, see `glitch`'s
    ROADMAP entry) - `bluescreen0r` was verified instead, cleanly keying
    a solid-color background to reveal the track below while leaving a
    differently-colored subject untouched. Unlike `reveal_style`, this
    spans the clip's whole duration (no fade_in/fade_out window,
    no keyframes needed) and needs no second track/qtblend-reveal setup
    of its own - it just composites through the same per-track qtblend
    chain every video track already gets against the ones below it."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "frei0r.bluescreen0r")
    _prop(f, "0", placement.chroma_key)  # Color
    _prop(f, "1", placement.chroma_key_distance)  # Distance
    _prop(f, "2", 0)  # Invert
    _prop(f, "kdenlive:collapsed", 0)


def _add_ken_burns_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Pan+zoom over a clip's whole duration via a single-clip `affine`
    filter with a keyframed `transition.rect` - the "transition." prefix
    is how a bare `affine` *filter* forwards properties to the `affine`
    *transition* it wraps internally (confirmed via `melt -query
    filter=affine`: its own parameter schema lists `transition.*` as "the
    primary means to use this filter"), and matches kdenlive's own
    "Position and Zoom" effect exactly - `~/dev/kdenlive/data/effects/
    pan_zoom.xml` uses the identical `transition.rect` animatedrect
    parameter, not modeled by analogy. Verified by real render: a 39px
    test square grew to 59px (1.51x, matching a 150% end rect) while
    staying centered on itself, over the keyframe window - an unprefixed
    `rect` property (like the affine *transition* used by scenes.py's
    `zoom`/`slide` uses directly) produces no animation at all when set
    on the *filter* instead.

    Two keyframes only - frame 0 (`ken_burns_from`) and the entry's own
    last frame (`ken_burns_to`), 0-relative to the entry's own `in`, same
    convention `_AFFINE_STYLE_RECT` above relies on. All-percentage
    units throughout (mixing absolute and percentage fields in the same
    rect silently misbehaves, same trap documented on
    `_AFFINE_STYLE_RECT`)."""
    length = placement.length_frames
    x0, y0, w0, h0 = placement.ken_burns_from
    x1, y1, w1, h1 = placement.ken_burns_to
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "affine")
    _prop(f, "kdenlive_id", "pan_zoom")
    _prop(
        f,
        "transition.rect",
        f"0={x0:g}%/{y0:g}%:{w0:g}%x{h0:g}%:100;{length - 1}={x1:g}%/{y1:g}%:{w1:g}%x{h1:g}%:100",
    )
    _prop(f, "transition.distort", 0)
    _prop(f, "kdenlive:collapsed", 0)


def _add_pillar_echo_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Focus-pull via MLT's own `pillar_echo` filter (kdenlive-native,
    Meltytech, not frei0r) - keeps `focus_rect` sharp, blurs everything
    outside it. Verified by real render before wiring in: a fine
    checkerboard test pattern measured a pixel-value stddev of ~127
    inside the rect vs. ~5-7 just outside it (a real, strong blur, not a
    dud like `broken-tv`/`spin`/`cube` turned out to be). Spans the
    clip's whole duration, no keyframes - same shape `chroma_key` uses,
    not `reveal_style`'s fade-window mechanism."""
    x, y, w, h = placement.focus_rect
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "pillar_echo")
    _prop(f, "kdenlive_id", "pillar_echo")
    _prop(f, "rect", f"{x:g}% {y:g}% {w:g}% {h:g}%")
    _prop(f, "blur", placement.focus_blur)
    _prop(f, "kdenlive:collapsed", 0)


def _add_freeze_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Freeze-frame via MLT's own `freeze` filter (kdenlive-native,
    LGPLv2.1, Jean-Baptiste Mardelle - matches kdenlive's own "Freeze"
    effect exactly, `~/dev/kdenlive/data/effects/freeze.xml`). Verified
    by real render before wiring in (`melt clip.mp4 -filter freeze
    frame=N freeze_after=1` etc. against a red/green two-segment test
    source): with both `freeze_before`/`freeze_after` left at their
    default 0, the *whole* clip holds frame N; `freeze_after=1` plays
    normally up to frame N and then holds it for the rest; `freeze_before=1`
    holds frame N's content from the start and plays normally from N
    onward - confirmed against MLT's own filter source,
    `~/dev/mlt/src/modules/kdenlive/filter_freeze.c`'s `do_freeze`
    condition, not just the XML effect definition. `frame` is 0-based
    from this clip's own first visible frame (its `in`/`out` trim
    already applied), matching MLT's own `pos = frame-property +
    producer's in`."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "freeze")
    _prop(f, "frame", placement.freeze_at_frame)
    if placement.freeze_before:
        _prop(f, "freeze_before", 1)
    if placement.freeze_after:
        _prop(f, "freeze_after", 1)
    _prop(f, "kdenlive:collapsed", 0)


def _add_old_film_filters(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """The "old film" look family (v0.8): four independent, freely
    combinable kdenlive-native filters (Marco Gittler, GPL, not
    frei0r), each a plain on/off toggle applying kdenlive's own filter
    defaults - no per-parameter overrides in this first cut, same
    narrow-scope call `overlay_text`'s fixed layout made. Confirmed
    present with these exact parameters via `melt -query filter=<name>`
    on both the from-source dev melt build and the system's stable one,
    and confirmed to produce a real, measurable, stable effect via real
    render (not assumed) -
    see `tests/test_melt_validation.py::test_old_film_produces_a_real_render_difference`.

    - `old_film` -> `oldfilm`: Y-jitter (`delta`/`every`) + brightness
      flicker (`brightnessdelta_*`) + patchy "uneven develop"
      (`unevendevelop_*`) - the strongest match for the "random black
      burned patches" look this whole feature was scoped around.
    - `film_dust` -> `dust`: scattered specks (`maxdiameter`/`maxcount`).
    - `film_scratches` -> `lines`: scratch lines (`line_width`/`num`/
      `darker`/`lighter`) - named "scratches" in the YAML schema since
      "lines" alone is a confusing, over-generic word for a user-facing
      field.
    - `film_grain` -> `grain`: film grain (`noise`/`contrast`/
      `brightness`).

    Applied after every other clip-level filter (fade/crop/chroma_key/
    overlay/Ken Burns/focus-pull), in this fixed order among themselves
    - order doesn't functionally matter between the four, but a
    consistent one keeps generated XML diffs stable."""
    if placement.old_film:
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
        _prop(f, "mlt_service", "oldfilm")
        _prop(f, "delta", 14)
        _prop(f, "every", 20)
        _prop(f, "brightnessdelta_up", 20)
        _prop(f, "brightnessdelta_down", 30)
        _prop(f, "brightnessdelta_every", 70)
        _prop(f, "unevendevelop_up", 60)
        _prop(f, "unevendevelop_down", 20)
        _prop(f, "unevendevelop_duration", 70)
        _prop(f, "kdenlive:collapsed", 0)
    if placement.film_dust:
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
        _prop(f, "mlt_service", "dust")
        _prop(f, "maxdiameter", 2)
        _prop(f, "maxcount", 10)
        _prop(f, "kdenlive:collapsed", 0)
    if placement.film_scratches:
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
        _prop(f, "mlt_service", "lines")
        _prop(f, "line_width", 2)
        _prop(f, "num", 5)
        _prop(f, "darker", 40)
        _prop(f, "lighter", 40)
        _prop(f, "kdenlive:collapsed", 0)
    if placement.film_grain:
        f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
        _prop(f, "mlt_service", "grain")
        _prop(f, "noise", 40)
        _prop(f, "contrast", 160)
        _prop(f, "brightness", 70)
        _prop(f, "kdenlive:collapsed", 0)


def _add_color_grade_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Professional color grading (v0.9 headline) via MLT's own
    `lift_gamma_gain` filter (Meltytech, LGPLv2.1 - "lift=shadows,
    gamma=midtones, gain=highlights", not an avfilter bridge, so no
    colorspace-bridging risk like the LUT-based alternative
    (`avfilter.lut3d`) hit - see the ROADMAP's "investigated and
    dropped" entry for that one, confirmed by a real render on this
    project's preferred melt build: an identity and an inverting `.cube`
    LUT both produced pixel-identical output to an unfiltered render,
    the same "Cannot set src colorspace" failure `avfilter.chromakey`/
    `avfilter.rgbashift` already ran into). Only the groups actually
    present in `color_grade` are written; an omitted group is left off
    the filter entirely so MLT's own neutral default for it applies
    (lift 0,0,0 / gamma 1,1,1 / gain 1,1,1 - together a no-op)."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "lift_gamma_gain")
    lift = placement.color_grade.get("lift")
    if lift is not None:
        _prop(f, "lift_r", lift[0])
        _prop(f, "lift_g", lift[1])
        _prop(f, "lift_b", lift[2])
    gamma = placement.color_grade.get("gamma")
    if gamma is not None:
        _prop(f, "gamma_r", gamma[0])
        _prop(f, "gamma_g", gamma[1])
        _prop(f, "gamma_b", gamma[2])
    gain = placement.color_grade.get("gain")
    if gain is not None:
        _prop(f, "gain_r", gain[0])
        _prop(f, "gain_g", gain[1])
        _prop(f, "gain_b", gain[2])
    _prop(f, "kdenlive:collapsed", 0)


def _add_deshake_filter(ids: _IdCounters, entry: ET.Element) -> None:
    """Video stabilization (v0.10 headline) via MLT's own `deshake`
    filter (Vid.Stab, GPLv2 - the single-pass version of `vidstab`, no
    intermediate `.trf` results file, so it fits this project's
    single-pass-static-XML architecture where `vidstab`'s two-pass
    analysis/apply workflow wouldn't). A plain on/off toggle: no
    properties set beyond `mlt_service`, letting MLT's own defaults
    apply for all eleven parameters (`shakiness`/`accuracy`/`stepsize`/
    `mincontrast`/`smoothing`/`maxshift`/`maxangle`/`crop`/`zoom`/
    `optzoom`/`zoomspeed`) - no per-parameter overrides in this first
    cut, same narrow-scope call `old_film`'s toggles made. Verified by
    real render before wiring in: a synthetic shaky source (a static
    high-contrast square viewed through an animated-crop "handheld
    camera" window) had its frame-to-frame feature-centroid movement
    reduced from a mean of ~18.9px/std 8.7 (unstabilized) to ~3.7px/std
    3.3 (stabilized) - identical results on both this project's
    preferred from-source dev melt build and the system's stable one,
    no `crop`/`lut3d`-style build-specific issue (see
    `tests/test_melt_validation.py::test_stabilize_reduces_frame_to_frame_jitter`)."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "deshake")
    _prop(f, "kdenlive:collapsed", 0)


def _hex_color(c: tuple[int, int, int, int]) -> str:
    r, g, b, a = c
    return f"0x{r:02x}{g:02x}{b:02x}{a:02x}"


def _add_overlay_text_filter(ids: _IdCounters, entry: ET.Element, placement: ResolvedPlacement) -> None:
    """Burned-in text overlay via MLT's own `dynamictext` filter (native,
    not avfilter-bridged - no colorspace issues like `chroma_key`/
    `glitch` ran into) on an *existing* clip - distinct from `titles.py`,
    which only produces standalone `type: title` bin clips. Supports
    `dynamictext`'s own keyword substitution (`#timecode#`, `#frame#`,
    `#localtime#`...) since `overlay_text` is passed through as-is.
    Verified by real render: white text on transparent background is
    clearly visible against a black test clip, and updates frame-by-frame
    for a keyword like `#timecode#`."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "dynamictext")
    _prop(f, "argument", placement.overlay_text)
    _prop(f, "geometry", "0%/0%:100%x100%:100")
    _prop(f, "family", placement.overlay_font)
    _prop(f, "size", placement.overlay_font_size)
    _prop(f, "fgcolour", _hex_color(placement.overlay_color))
    _prop(f, "bgcolour", _hex_color(placement.overlay_background))
    _prop(f, "halign", "centre")
    _prop(f, "valign", "middle")
    _prop(f, "kdenlive:collapsed", 0)


def _add_mute_filter(ids: _IdCounters, entry: ET.Element) -> None:
    """Matches kdenlive's own "Mute" effect (share/kdenlive/effects/mute.xml):
    mlt_service "volume", kdenlive_id "mute", a fixed gain of 0 with no
    in/out span - it silences the entry's full duration, video-track
    clips included (the filter only touches audio samples, so the picture
    still renders; this is what lets a video clip's embedded audio be
    silenced independently of its image)."""
    f = ET.SubElement(entry, "filter", {"id": ids.next("filter")})
    _prop(f, "mlt_service", "volume")
    _prop(f, "kdenlive_id", "mute")
    _prop(f, "gain", 0)
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
        # The chain gets the clip's *full* source range, not the placement's
        # trim: a playlist <entry>'s in/out are relative to the producer's
        # own in point, not absolute frame numbers (confirmed upstream:
        # https://github.com/mltframework/mlt/issues/1282). If the chain
        # itself were also trimmed to in_frame..out_frame, the entry's
        # in_frame would be added on top of it, reading from 2x in_frame
        # instead of in_frame. With `speed`, "full range" means the
        # *warped* duration (schema.py already computed this the same
        # way when validating in/out/duration).
        if placement.speed_ramp is not None:
            # A ramp always starts at frame 0 (no separate `in:` trim -
            # see schema.py) and its own full range *is* the placement's
            # resolved duration, unlike flat `speed:` where "full range"
            # is a separately-computed warped length that the entry then
            # sub-trims via in_frame/out_frame.
            full_duration_frames = placement.out_frame + 1
        elif placement.speed:
            full_duration_frames = round(placement.bin_clip.duration_frames / abs(placement.speed))
        else:
            full_duration_frames = placement.bin_clip.duration_frames
        el, xml_id = _build_media_element(
            ids,
            path=placement.bin_clip.path,
            kind=placement.bin_clip.kind,
            in_frame=0,
            out_frame=full_duration_frames - 1,
            numeric_id=placement.bin_clip.numeric_id,
            control_uuid=control_uuid_for[placement.bin_clip.id],
            speed=placement.speed,
            preserve_pitch=placement.preserve_pitch,
            speed_ramp=placement.speed_ramp,
            speed_ramp_image_mode=placement.speed_ramp_image_mode,
            fps=project.fps.fps,
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
            if placement.reveal_style:
                _add_reveal_style_filter(ids, entry, placement)
            else:
                _add_video_fade_filters(ids, entry, placement)
            if placement.crop is not None:
                _add_crop_filters(ids, entry, placement.crop)
            _add_video_default_filter(
                ids, entry, project.width, project.height, placement.rect, placement.position_keyframes
            )
            if placement.chroma_key:
                _add_chroma_key_filter(ids, entry, placement)
            if placement.overlay_text:
                _add_overlay_text_filter(ids, entry, placement)
            if placement.ken_burns_from is not None:
                _add_ken_burns_filter(ids, entry, placement)
            if placement.focus_rect is not None:
                _add_pillar_echo_filter(ids, entry, placement)
            if placement.freeze_at_frame is not None:
                _add_freeze_filter(ids, entry, placement)
            _add_old_film_filters(ids, entry, placement)
            if placement.color_grade is not None:
                _add_color_grade_filter(ids, entry, placement)
            if placement.stabilize:
                _add_deshake_filter(ids, entry)
        if placement.mute:
            _add_mute_filter(ids, entry)
        cursor = placement.start_frame + placement.length_frames

    root.append(playlist_main)

    playlist_secondary = ET.Element("playlist", {"id": ids.next("playlist")})
    if is_audio:
        _prop(playlist_secondary, "kdenlive:audio_track", 1)
    root.append(playlist_secondary)

    track_tractor = ET.Element("tractor", {"id": ids.next("tractor")})
    if is_audio:
        _prop(track_tractor, "kdenlive:audio_track", 1)
    # Confirmed against kdenlive's own source (TrackModel::TrackModel,
    # src/timeline2/model/trackmodel.cpp: `m_track->set("kdenlive:track_name",
    # ...)` on the track's own tractor) - not modeled by analogy.
    _prop(track_tractor, "kdenlive:track_name", track.name)
    if track.hidden or track.audio_muted or track.locked:
        # `hide` here is the track's own tractor bitmask (bit 1 = video
        # hidden, bit 2 = audio muted - TrackModel::isHidden/isMute in
        # kdenlive's source), not the `hide="video"/"audio"` attribute on
        # the <track> sub-elements below (that one tags which of the two
        # internal mix-feature playlists carries which signal type, a
        # separate, unrelated mechanism this generator already emits
        # unconditionally). Only written when true - a real kdenlive
        # project doesn't write either property until a user actually
        # toggles visibility/mute/lock, so omitting them for the default
        # (visible, unmuted, unlocked) case matches that.
        hide_bits = (1 if track.hidden else 0) | (2 if track.audio_muted else 0)
        if hide_bits:
            _prop(track_tractor, "hide", hide_bits)
        if track.locked:
            _prop(track_tractor, "kdenlive:locked_track", 1)
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
        if track.pan is not None:
            # channel=-1 is stereo balance (confirmed via `melt -query
            # filter=panner`): start 0 is full left, 0.5 center, 1 full
            # right - `pan` is -1..1, same shape as MLT's `start` just
            # re-centered. `disable=1` is what makes this filter inert
            # (kdenlive still writes the internal_added=237 mixer-strip
            # placeholder even once a real value has been set, the same
            # way it stays present but active once a user actually moves
            # the balance knob) - only cleared once a real pan is given.
            _prop(f2, "start", (track.pan + 1) / 2)
            _prop(f2, "disable", 0)
        else:
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
    # JSON array of {"pos": frame, "comment": str, "type": category} -
    # confirmed against kdenlive's own source (MarkerListModel::toJson,
    # src/bin/model/markerlistmodel.cpp), not modeled by analogy.
    guides_json = [
        {"pos": g.frame, "comment": g.text, "type": g.category} for g in project.guides
    ]
    _prop(sequence, "kdenlive:sequenceproperties.guides", json.dumps(guides_json))

    ET.SubElement(sequence, "track", {"producer": "producer_black"})
    for _, tid in track_tractor_ids:
        ET.SubElement(sequence, "track", {"producer": tid})

    if project.wipe_transitions or project.affine_transitions:
        # Must come before the a_track=0 compositing transitions below: MLT
        # applies a tractor's transitions in declaration order, and (found
        # empirically - see tests/test_melt_validation.py's wipe test)
        # once qtblend has already run against a track, a later luma (or
        # affine) transition between two *other* tracks stops progressing
        # over time at all (every sampled frame lands on the same, wrong
        # side of the transition, as if `in`/`out` were ignored). Putting
        # these transitions first avoids whatever state qtblend's
        # compositing disturbs.
        track_position = {track.name: pos for pos, track in enumerate(project.tracks, start=1)}
        for w in project.wipe_transitions:
            t = ET.SubElement(
                sequence,
                "transition",
                {"id": ids.next("transition"), "in": str(w.start_frame), "out": str(w.end_frame)},
            )
            _prop(t, "a_track", track_position[w.track_a])
            _prop(t, "b_track", track_position[w.track_b])
            _prop(t, "mlt_service", "luma")
            _prop(t, "kdenlive_id", "luma")
            _prop(t, "resource", str(lumas.luma_path(w.luma)))
            _prop(t, "softness", 0)
            _prop(t, "alpha_over", 1)
            _prop(t, "fix_background_alpha", 1)
        for a in project.affine_transitions:
            t = ET.SubElement(
                sequence,
                "transition",
                {"id": ids.next("transition"), "in": str(a.start_frame), "out": str(a.end_frame)},
            )
            _prop(t, "a_track", track_position[a.track_a])
            _prop(t, "b_track", track_position[a.track_b])
            _prop(t, "mlt_service", "affine")
            _prop(t, "kdenlive_id", "affine")
            _prop(t, "rect", _AFFINE_STYLE_RECT[a.style].format(first=0, last=a.end_frame - a.start_frame))
            _prop(t, "fill", 1)
            _prop(t, "distort", 0)

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

    # Deterministic folder ids for `folder:` on bin clips (root-level
    # only) - sorted by name so output doesn't depend on YAML ordering.
    # Starts at 3: id 2 is already claimed by the "Secuencias" folder
    # every project gets below (kdenlive:sequenceFolder) - confirmed
    # against kdenlive's own source (BinPlaylist::manageBinItemInsertion,
    # `kdenlive:folder.<parentId>.<id>` = name, set on `main_bin`), not
    # modeled by analogy.
    folder_names = sorted({c.folder for c in project.bin_clips if c.folder})
    folder_ids = {name: i + 3 for i, name in enumerate(folder_names)}

    # "Master" bin chains (one full-length reference per asset, for the Project Bin listing).
    bin_chain_ids: dict[str, str] = {}
    for clip in project.bin_clips:
        el, xml_id = _build_media_element(
            ids,
            path=clip.path,
            kind=clip.kind,
            in_frame=0,
            out_frame=clip.duration_frames - 1,
            numeric_id=clip.numeric_id,
            control_uuid=control_uuid_for[clip.id],
            folder_id=folder_ids.get(clip.folder, -1),
        )
        root.append(el)
        bin_chain_ids[clip.id] = xml_id

    main_bin = ET.Element("playlist", {"id": "main_bin"})
    _prop(main_bin, "kdenlive:folder.-1.2", "Secuencias")
    for name, fid in folder_ids.items():
        _prop(main_bin, f"kdenlive:folder.-1.{fid}", name)
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
    if project.notes:
        # Confirmed against kdenlive's own source
        # (ProjectManager::slotSaveProject -> saveProperty("kdenlive:
        # documentnotes", ...), src/project/projectmanager.cpp) - a plain
        # string property on main_bin, not modeled by analogy. Only
        # written when set: a fresh kdenlive project doesn't write this
        # property at all until a user actually enters notes.
        _prop(main_bin, "kdenlive:documentnotes", project.notes)
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
