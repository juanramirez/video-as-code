"""Data structures: what's read from the YAML (Spec) and what the XML
generator consumes once validated and resolved to frames (Resolved)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .timecode import FrameRate

VALID_TRACK_TYPES = ("video", "audio")
VALID_CLIP_TYPES = ("video", "audio", "image")


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
    path: Path
    type: Optional[str] = None  # overrides the extension-based autodetection


@dataclass
class ClipPlacementSpec:
    clip: str
    start: float
    in_: float = 0.0
    out: Optional[float] = None
    duration: Optional[float] = None
    fade_in: Optional[float] = None
    fade_out: Optional[float] = None
    position: Optional[tuple[float, float]] = None
    size: Optional[tuple[float, float]] = None


@dataclass
class TrackSpec:
    type: str
    name: Optional[str]
    clips: list[ClipPlacementSpec] = field(default_factory=list)


@dataclass
class ProjectDoc:
    project: ProjectSpec
    bin: list[BinClipSpec]
    tracks: list[TrackSpec]
    source_path: Path
    raw_extras: Optional[dict] = None  # see reverse.py: `x_kdenlive_raw` block
    scenes_raw: list = field(default_factory=list)  # uncompiled; see scenes.py


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
    rect: Optional[tuple[float, float, float, float]] = None  # x y w h in pixels

    @property
    def length_frames(self) -> int:
        return self.out_frame - self.in_frame + 1


@dataclass
class ResolvedTrack:
    kind: str
    name: str
    placements: list[ResolvedPlacement]

    @property
    def end_frame(self) -> int:
        if not self.placements:
            return 0
        last = self.placements[-1]
        return last.start_frame + last.length_frames


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

    @property
    def total_frames(self) -> int:
        return max((t.end_frame for t in self.tracks), default=0)
