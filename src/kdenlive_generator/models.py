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


@dataclass
class BinClipSpec:
    id: str
    path: Optional[Path]  # None for type: title - schema.py generates and fills it in
    type: Optional[str] = None  # overrides the extension-based autodetection
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


@dataclass
class TrackSpec:
    type: str
    name: Optional[str]
    clips: list[ClipPlacementSpec] = field(default_factory=list)
    pan: Optional[float] = None  # audio only: -1.0 (full left) .. 1.0 (full right)


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
class ProjectDoc:
    project: ProjectSpec
    bin: list[BinClipSpec]
    tracks: list[TrackSpec]
    source_path: Path
    raw_extras: Optional[dict] = None  # see reverse.py: `x_kdenlive_raw` block
    scenes_raw: list = field(default_factory=list)  # uncompiled; see scenes.py
    wipe_transitions: list[WipeTransitionSpec] = field(default_factory=list)
    affine_transitions: list[AffineTransitionSpec] = field(default_factory=list)


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

    @property
    def length_frames(self) -> int:
        return self.out_frame - self.in_frame + 1


@dataclass
class ResolvedTrack:
    kind: str
    name: str
    placements: list[ResolvedPlacement]
    pan: Optional[float] = None

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

    @property
    def total_frames(self) -> int:
        return max((t.end_frame for t in self.tracks), default=0)
