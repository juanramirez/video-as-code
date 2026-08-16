"""Data structures: what's read from the YAML (Spec) and what the XML
generator consumes once validated and resolved to frames (Resolved)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .timecode import FrameRate

VALID_TRACK_TYPES = ("video", "audio")
VALID_CLIP_TYPES = ("video", "audio", "image", "title")


# ---------------------------------------------------------------------------
# As-is from the YAML (units: seconds, paths already resolved to Path)
# ---------------------------------------------------------------------------

@dataclass
class ProjectSpec:
    id: str
    title: str
    fps: float
    width: int
    height: int
    colorspace: int = 709
    default_image_duration: float = 4.0
    notes: Optional[str] = None


@dataclass
class BinClipSpec:
    id: str
    path: Optional[Path]  # None for type: title - schema.py generates and fills it in
    type: Optional[str] = None  # overrides the extension-based autodetection
    folder: Optional[str] = None  # bin folder name (root-level only), None = no folder
    # title-only fields (type: title), ignored otherwise:
    text: Optional[str] = None
    font: str = "Sans"
    font_size: int = 48
    font_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    background: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass
class ClipPlacementSpec:
    clip: str
    start: float
    in_: float = 0.0
    out: Optional[float] = None
    duration: Optional[float] = None
    fade_in: Optional[float] = None
    fade_out: Optional[float] = None
    mute: bool = False
    speed: Optional[float] = None
    preserve_pitch: bool = False
    position: Optional[tuple[float, float]] = None
    size: Optional[tuple[float, float]] = None
    reveal_style: Optional[str] = None  # None | "iris" | "flip" | "glitch" - see ResolvedPlacement
    chroma_key: Optional[str] = None  # color to key out (e.g. "#00ff00"), None = no chroma-key
    chroma_key_distance: float = 0.35  # frei0r.bluescreen0r "Distance" - only used when chroma_key is set
    # Burned-in text overlay (mlt_service "dynamictext") on an *existing*
    # clip - distinct from titles.py's standalone type: title bin clips.
    # `overlay_text` may include dynamictext's own keywords (#timecode#,
    # #frame#, #localtime#...), same as MLT itself.
    overlay_text: Optional[str] = None
    overlay_font: str = "Sans"
    overlay_font_size: int = 48
    overlay_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    overlay_background: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Ken Burns pan+zoom (mlt_service "affine" as a single-clip filter,
    # same mechanism kdenlive's own "Position and Zoom" effect uses -
    # see xmlgen.py's _add_ken_burns_filter). Each is (x%, y%, w%, h%) of
    # the frame - the filter animates from `ken_burns_from` at the clip's
    # start to `ken_burns_to` at its end. Both or neither, never just one.
    ken_burns_from: Optional[tuple[float, float, float, float]] = None
    ken_burns_to: Optional[tuple[float, float, float, float]] = None


@dataclass
class TrackSpec:
    type: str
    name: Optional[str]
    clips: list[ClipPlacementSpec] = field(default_factory=list)
    pan: Optional[float] = None  # audio only: -1.0 (full left) .. 1.0 (full right)
    hidden: bool = False
    locked: bool = False
    audio_muted: bool = False  # whole-track audio mute (hide's bit 2) - distinct from per-clip mute:


@dataclass
class WipeTransitionSpec:
    """One overlap window between two adjacent clips on alternating video
    tracks, produced by scenes.py's `transition: wipe`. Unlike `dissolve`
    (a per-clip alpha filter fakes the cross-fade), a wipe needs a real
    two-source MLT transition (`mlt_service: luma`) so the luma map can
    decide, per pixel, which of the two overlapping clips shows - so this
    carries enough to let xmlgen.py emit that transition directly, instead
    of being folded into `ClipPlacementSpec` like a fade is."""
    track_a: str
    track_b: str
    start: float
    end: float
    luma: str  # one of lumas.AVAILABLE_LUMAS


AFFINE_STYLES = ("zoom", "slide")  # shared with scenes.py and schema.py's `transitions:` parsing


@dataclass
class AffineTransitionSpec:
    """One overlap window between two adjacent clips on alternating video
    tracks, produced by scenes.py's `transition: affine` (`style: zoom` or
    `style: slide`). Like `wipe`, this needs a real two-source MLT
    transition (`mlt_service: affine`) rather than a per-clip alpha
    filter, since it geometrically transforms the incoming clip (scaled
    rect for zoom, offset rect for slide) as it's composited over the
    outgoing one - a per-clip filter can't do a two-source geometric
    reveal any more than it could a two-source luma wipe."""
    track_a: str
    track_b: str
    start: float
    end: float
    style: str  # "zoom" | "slide"


@dataclass
class GuideSpec:
    """A timeline marker/guide - `kdenlive:sequenceproperties.guides` on the
    sequence tractor, a JSON array of `{"pos": <frame>, "comment": <str>,
    "type": <int>}` (confirmed against kdenlive's own source,
    `MarkerListModel::toJson`/`importFromJson` in
    src/bin/model/markerlistmodel.cpp - not modeled by analogy). `category`
    is kdenlive's marker-category index (color-coded in the UI); 0 is
    always a valid fallback category."""
    at: float
    text: str
    category: int = 0


@dataclass
class ProjectDoc:
    project: ProjectSpec
    bin: list[BinClipSpec]
    tracks: list[TrackSpec]
    source_path: Path
    raw_extras: Optional[dict] = None  # see reverse.py: `x_kdenlive_raw` block
    scenes_raw: list = field(default_factory=list)  # uncompiled; see scenes.py
    wipe_transitions: list[WipeTransitionSpec] = field(default_factory=list)
    affine_transitions: list[AffineTransitionSpec] = field(default_factory=list)
    guides: list[GuideSpec] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validated and resolved to frames: what xmlgen.py consumes
# ---------------------------------------------------------------------------

@dataclass
class ResolvedBinClip:
    id: str
    numeric_id: int
    path: Path
    kind: str  # video | audio | image
    duration_frames: int
    folder: Optional[str] = None


@dataclass
class ResolvedPlacement:
    bin_clip: ResolvedBinClip
    start_frame: int
    in_frame: int
    out_frame: int  # last included frame (MLT convention)
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    mute: bool = False
    speed: Optional[float] = None
    preserve_pitch: bool = False
    rect: Optional[tuple[float, float, float, float]] = None  # x y w h in pixels
    # which filter `fade_in_frames`/`fade_out_frames` should drive instead
    # of the plain brightness alpha fade. "iris" (frei0r.alphaspot) and
    # "flip" (frei0r.3dflippo) are real two-source-*looking* reveals built
    # from a single-clip filter composited via the qtblend this project
    # already emits per video track, same mechanism `dissolve` itself
    # uses (only the "top" clip needs the filter). "glitch"
    # (frei0r.glitch0r) is different again - it writes no alpha at all,
    # so it isn't a reveal/composite in any sense, just a brief distortion
    # burst dressing a plain cut (see scenes.py's `GLITCH_TRACK_NAME`).
    reveal_style: Optional[str] = None  # None | "iris" | "flip" | "glitch"
    # Green/blue-screen compositing (frei0r.bluescreen0r) - unlike
    # reveal_style, this spans the clip's *whole* duration, not a brief
    # transition window, and composites through the same per-track
    # qtblend chain every video track already gets against the ones
    # below it (no new mechanism needed - verified by real render).
    chroma_key: Optional[str] = None
    chroma_key_distance: float = 0.35
    # Burned-in text overlay (mlt_service "dynamictext") - see
    # ClipPlacementSpec.
    overlay_text: Optional[str] = None
    overlay_font: str = "Sans"
    overlay_font_size: int = 48
    overlay_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    overlay_background: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Ken Burns pan+zoom - see ClipPlacementSpec.
    ken_burns_from: Optional[tuple[float, float, float, float]] = None
    ken_burns_to: Optional[tuple[float, float, float, float]] = None

    @property
    def length_frames(self) -> int:
        return self.out_frame - self.in_frame + 1


@dataclass
class ResolvedTrack:
    kind: str
    name: str
    placements: list[ResolvedPlacement]
    pan: Optional[float] = None
    hidden: bool = False
    locked: bool = False
    audio_muted: bool = False

    @property
    def end_frame(self) -> int:
        if not self.placements:
            return 0
        last = self.placements[-1]
        return last.start_frame + last.length_frames


@dataclass
class ResolvedWipeTransition:
    track_a: str
    track_b: str
    start_frame: int
    end_frame: int  # last included frame (MLT convention, like ResolvedPlacement.out_frame)
    luma: str


@dataclass
class ResolvedAffineTransition:
    track_a: str
    track_b: str
    start_frame: int
    end_frame: int  # last included frame (MLT convention, like ResolvedPlacement.out_frame)
    style: str  # "zoom" | "slide"


@dataclass
class ResolvedGuide:
    frame: int
    text: str
    category: int


@dataclass
class ResolvedProject:
    project_id: str
    title: str
    fps: FrameRate
    width: int
    height: int
    colorspace: int
    bin_clips: list[ResolvedBinClip]
    tracks: list[ResolvedTrack]
    source_dir: Path
    wipe_transitions: list[ResolvedWipeTransition] = field(default_factory=list)
    affine_transitions: list[ResolvedAffineTransition] = field(default_factory=list)
    guides: list[ResolvedGuide] = field(default_factory=list)
    notes: Optional[str] = None

    @property
    def total_frames(self) -> int:
        return max((t.end_frame for t in self.tracks), default=0)
