"""`build`/`render --proxy WxH`: light copies of only the source ranges
the timeline uses, so a preview renders within a small machine's RAM and
in a fraction of the time.

Full-resolution phone videos (HEVC, 1080p-4K) are what makes melt run out
of memory on a laptop: it builds every producer at load. A light copy
(H.264 at e.g. 480x270) fixes that, but a light copy of a WHOLE 2-minute
video of which 4 s are used still decodes all 2 minutes to make, and
keeps them on disk. So this copies only what the placements read:

1. For every video file a resolved placement reads, the source-second
   ranges it reads (flat `speed:` and `speed_ramp:` included - see
   _source_range), grouped into clusters (ranges less than CLUSTER_GAP_S
   apart share one copy; far-apart uses of one long video get one copy
   each), each padded by MARGIN_S on both sides.
2. Each cluster becomes a cached copy (derive.py's own trim + scale, in
   <project dir>/.proxies/, regenerated only if its range or input
   changes).
3. Each placement is pointed at its cluster's copy, with its source
   times shifted by where the copy starts.

A welcome side effect: every copy starts near the part that's used, so a
large `source_in` with `speed:` - which freezes MLT's timewarp producer
and makes projects keep hand-trimmed copies of their videos - doesn't
happen in a proxied render.

Left alone: stills, gifs (an image producer, whose `crop:` margins are
in the file's own pixels - shrinking it would shift them) and audio; a
video read backwards (negative `speed:`). A copy that fails is reported
(and `build`/`render` stop).

A video's `crop:` margins are in the file's own pixels too (measured: the
same margins on a 1280x720 file and its 480x270 copy frame differently),
so a proxied placement's crop is scaled by copy size / original size.
Colours stay right too: a tagged video (phones tag theirs) keeps its tags
in the copy, and an untagged one gets its colour matrix converted when
the copy drops below 720 lines (see derive.build_command).

Where a flat `speed:` placement starts in its copy is rounded to a whole
timeline frame of the warped clip, so it can start up to half a frame
of that (speed/fps/2 source seconds) off - invisible in a preview, and
why this is for previews, not the final render.
"""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from . import derive
from .i18n import msg
from .models import DeriveSpec, ResolvedBinClip, ResolvedPlacement, ResolvedProject

PROXY_DIR = ".proxies"
MARGIN_S = 1.0
CLUSTER_GAP_S = 5.0
# a bin clip no placement reads still gets a producer: give it a tiny copy
UNUSED_S = 1.0


def parse_size(text: str) -> tuple[int, int]:
    """'480x270' -> (480, 270). Raises ValueError."""
    w, sep, h = text.lower().partition("x")
    if not sep:
        raise ValueError(text)
    size = (int(w), int(h))
    if min(size) <= 0 or any(v % 2 for v in size):  # (H.264 needs even sizes)
        raise ValueError(text)
    return size


def _proxyable(clip: ResolvedBinClip) -> bool:
    return clip.kind == "video" and clip.path.suffix.lower() != ".gif"


def _ramp_source_seconds(ramp: list[tuple[int, float]], out_frames: int, fps: float) -> float:
    """Source seconds a speed ramp reads in its `out_frames` timeline
    frames (each keyframe's speed holds until the next one)."""
    total = 0.0
    for i, (at, speed) in enumerate(ramp):
        end = ramp[i + 1][0] if i + 1 < len(ramp) else out_frames
        total += max(min(end, out_frames) - at, 0) / fps * abs(speed)
    return total


def _source_range(p: ResolvedPlacement, fps: float) -> tuple[float, float]:
    """[start, end) in source seconds that placement `p` reads."""
    if p.speed_ramp is not None:
        # a ramp reads from its source offset, starting at timeline frame 0
        start = p.speed_ramp_source_offset_s
        return start, start + _ramp_source_seconds(p.speed_ramp, p.out_frame + 1, fps)
    speed = abs(p.speed) if p.speed else 1.0
    return p.in_frame / fps * speed, (p.out_frame + 1) / fps * speed


def _clusters(ranges: list[tuple[float, float]], duration_s: float) -> list[tuple[float, float]]:
    """Merged, padded, clamped [start, end) spans covering `ranges`."""
    merged: list[list[float]] = []
    for start, end in sorted(ranges):
        if merged and start - merged[-1][1] <= CLUSTER_GAP_S:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(max(s - MARGIN_S, 0.0), min(e + MARGIN_S, duration_s)) for s, e in merged]


def _shifted(
    p: ResolvedPlacement,
    copy: ResolvedBinClip,
    start_s: float,
    fps: float,
    scale: tuple[float, float],
) -> ResolvedPlacement:
    """`p` reading `copy`, which starts `start_s` into the original and is
    `scale` (x, y) times its size."""
    if p.crop is not None:
        top, right, bottom, left = p.crop
        sx, sy = scale
        p = replace(p, crop=(top * sy, right * sx, bottom * sy, left * sx))
    if p.speed_ramp is not None:
        return replace(p, bin_clip=copy, speed_ramp_source_offset_s=p.speed_ramp_source_offset_s - start_s)
    speed = abs(p.speed) if p.speed else 1.0
    # in the placement's own (warped, with speed) frames; start_s is on a
    # timeline frame (see apply), so this is exact without `speed`
    shift = round(start_s * fps / speed)
    return replace(p, bin_clip=copy, in_frame=p.in_frame - shift, out_frame=p.out_frame - shift)


def apply(
    project: ResolvedProject,
    size: tuple[int, int],
    errors: list[str],
    on_progress: Optional[Callable[[str], None]] = None,
) -> ResolvedProject:
    """`project` with its videos' placements reading light copies of the
    ranges they use (see the module docstring). A copy that can't be made
    is appended to `errors` (and that video keeps its original)."""
    report = on_progress or (lambda text: None)
    # a project in several files (parts.py): each part's own, in its own
    # folder, and the part placed by its new version
    nested = {
        id(clip): replace(clip, sequence=apply(clip.sequence, size, errors, on_progress))
        for clip in project.bin_clips if clip.sequence is not None
    }
    if nested:
        project = replace(
            project,
            bin_clips=[nested.get(id(c), c) for c in project.bin_clips],
            tracks=[
                replace(t, placements=[replace(p, bin_clip=nested.get(id(p.bin_clip), p.bin_clip)) for p in t.placements])
                for t in project.tracks
            ],
        )
    fps = project.fps.fps
    proxy_dir = project.source_dir / PROXY_DIR
    w, h = size

    # every video file a placement reads (a boomerang's pre-reversed copy
    # is a file of its own, under the same bin id), with the ranges read
    reads: dict[Path, list[tuple[float, float]]] = {}
    clip_of: dict[Path, ResolvedBinClip] = {}
    for track in project.tracks:
        for p in track.placements:
            if _proxyable(p.bin_clip):
                clip_of.setdefault(p.bin_clip.path, p.bin_clip)
                reads.setdefault(p.bin_clip.path, []).append(_source_range(p, fps))
    backwards = {
        p.bin_clip.path for t in project.tracks for p in t.placements if p.speed is not None and p.speed < 0
    }
    for clip in project.bin_clips:
        if _proxyable(clip) and clip.path not in reads:
            clip_of[clip.path] = clip
            reads[clip.path] = [(0.0, min(UNUSED_S, clip.duration_frames / fps))]

    # copy size / original size, per original (for crop: margins)
    scale_of: dict[Path, tuple[float, float]] = {}
    # path -> [(start_s, end_s, copy)] in source order
    copies: dict[Path, list[tuple[float, float, ResolvedBinClip]]] = {}
    jobs = [(path, span) for path in reads if path not in backwards
            for span in _clusters(reads[path], clip_of[path].duration_frames / fps)]
    for n, (path, (start, end)) in enumerate(jobs, start=1):
        clip = clip_of[path]
        # starts on a timeline frame, so unwarped placements shift exactly
        start = math.floor(start * fps) / fps
        spec = DeriveSpec(path=path, trim=(start, end), scale=(w, h))
        # one name per copy: derive.generate deletes other generations of
        # the same name, which would otherwise be this video's other copies
        # (a part's ids carry its path - parts.py: no folders in a file name)
        name = f"{clip.id.replace('/', '_').replace(':', '_')}~{start:g}"
        output = derive.output_path(name, spec, path, f"path:{path.name}", proxy_dir)
        if not derive.is_current(output, path):
            report(msg("proxy.making", n=n, total=len(jobs), id=clip.id, start=start, end=end))
            try:
                derive.generate(name, spec, path, output)
            except derive.DeriveError as exc:
                errors.append(str(exc))
                continue
        copy = replace(clip, path=output, duration_frames=max(round((end - start) * fps), 1))
        copies.setdefault(path, []).append((start, end, copy))
        if path not in scale_of:
            info = derive.video_info(path) or (w, h, True)
            scale_of[path] = (w / info[0], h / info[1])

    def pick(path: Path, read: tuple[float, float]) -> Optional[tuple[float, float, ResolvedBinClip]]:
        for span in copies.get(path, []):
            if span[0] <= read[0] + 1e-6 and read[1] <= span[1] + 1e-6:
                return span
        return None

    tracks = []
    for track in project.tracks:
        placements = []
        for p in track.placements:
            chosen = pick(p.bin_clip.path, _source_range(p, fps)) if _proxyable(p.bin_clip) else None
            placements.append(_shifted(p, chosen[2], chosen[0], fps, scale_of[p.bin_clip.path]) if chosen else p)
        tracks.append(replace(track, placements=placements))
    bin_clips = [copies[c.path][0][2] if c.path in copies else c for c in project.bin_clips]
    return replace(project, tracks=tracks, bin_clips=bin_clips)
