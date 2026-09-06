"""Post-resolve, pre-xmlgen pass that repacks a `ResolvedProject`'s tracks
onto the fewest physical tracks possible, for the `build --pack-tracks` CLI
flag. Purely a presentation/editing-convenience transform - it changes how
many `<track>`/`<tractor>` "lines" a human sees when opening the generated
`.kdenlive` in the Kdenlive app, not the rendered result: every placement
keeps its exact `start_frame`/`in_frame`/`out_frame` and every other field
unchanged, only which output track it lands on is renumbered.

## Why order matters for video, not for audio

Every video track this project generates gets a `qtblend` transition against
`a_track=0` (xmlgen.py's `generate()`), and MLT applies those in declaration
order - each later-declared track draws *over* the accumulated result of
every earlier one. So when two placements overlap in time (a crossfade hand-
off - see `_add_video_fade_filters`'s "alpha follows level" mechanism and
scenes.py's dissolve docstring), whichever one keeps the higher track index
after packing must still be the one that was higher *before* packing, or the
composite comes out inverted (the older clip would incorrectly cover the
newer one instead of being revealed by it).

Audio tracks instead get a `mix` transition (`sum=1`) - summing is
commutative, so audio placements can be packed onto the fewest tracks with a
plain interval-graph coloring, no order constraint at all.

## Declaration order, not start time

"Which one must stay on top" is decided by *declaration* order (a track's
position in `timeline.tracks:`), not by which placement starts earlier -
this project's own `header` track starts at 0s but must still render above
`intro_audio`, which starts at 0.5s but was declared first. So packing
processes placements in declaration-order (each original track's index),
not sorted by start time: by the time a given item is being placed, every
item already placed is guaranteed to have a *lower* declared priority, so
the only constraint ever needed is "stay above anything already placed that
overlaps this one in time" - never a "stay below something not placed yet"
constraint, which would need knowing the future.

## What's left alone

A track referenced by name in a `wipe`/`affine` transition (`Resolved
WipeTransition`/`ResolvedAffineTransition.track_a`/`track_b`) can't be
merged into another track without breaking that reference, and a track with
a non-default `hidden`/`locked`/`audio_muted`/`pan` can't be silently merged
with one that doesn't share that same setting without losing it. Those
tracks are left exactly as they were - all their placements together, still
getting their own dedicated output track - but that track's whole active
span still counts as occupied for the ordering/occupancy accounting below,
so packed placements around it still end up correctly ordered relative to
it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .models import ResolvedPlacement, ResolvedProject, ResolvedTrack


@dataclass
class _Item:
    start: int
    end: int  # exclusive
    priority: int  # original track's position among tracks of this kind
    orig_track_index: int
    pinned: bool  # True: represents a whole ineligible track, not one placement
    placement: ResolvedPlacement | None = None  # set when not pinned


@dataclass
class _Slot:
    output_track: ResolvedTrack
    intervals: list[tuple[int, int]]  # every [start, end) already placed here
    pinned_to: int | None = None


def _new_track_like(template: ResolvedTrack) -> ResolvedTrack:
    return ResolvedTrack(
        kind=template.kind,
        name=template.name,
        placements=[],
        pan=template.pan,
        hidden=template.hidden,
        locked=template.locked,
        audio_muted=template.audio_muted,
    )


def _overlaps(intervals: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(s < end and start < e for s, e in intervals)


def _pack_kind(tracks: list[ResolvedTrack], ineligible: set[int], preserve_order: bool) -> list[ResolvedTrack]:
    items: list[_Item] = []
    for i, track in enumerate(tracks):
        if not track.placements:
            continue
        if i in ineligible:
            items.append(_Item(track.placements[0].start_frame, track.end_frame, i, i, pinned=True))
        else:
            for p in track.placements:
                items.append(_Item(p.start_frame, p.start_frame + p.length_frames, i, i, pinned=False, placement=p))
    items.sort(key=lambda it: it.priority)

    slots: list[_Slot] = []

    for it in items:
        min_index = 0
        if preserve_order:
            for idx, s in enumerate(slots):
                if _overlaps(s.intervals, it.start, it.end):
                    min_index = max(min_index, idx + 1)

        target = None
        for idx in range(min_index, len(slots)):
            s = slots[idx]
            if s.pinned_to is None and not _overlaps(s.intervals, it.start, it.end):
                target = idx
                break
        if target is None:
            target = len(slots)
            slots.append(_Slot(_new_track_like(tracks[it.orig_track_index]), []))

        s = slots[target]
        s.intervals.append((it.start, it.end))
        if it.pinned:
            s.pinned_to = it.orig_track_index
            s.output_track.placements = list(tracks[it.orig_track_index].placements)
        else:
            assert it.placement is not None
            s.output_track.placements.append(it.placement)

    return [s.output_track for s in slots]


def pack_tracks(project: ResolvedProject) -> ResolvedProject:
    """Returns a copy of `project` with its tracks repacked onto the fewest
    possible, preserving every placement's timing/fields and the relative
    compositing order between any two that overlap in time (see module
    docstring). Track names are inherited from whichever original track a
    packed track's placements came from - cosmetic only, xmlgen.py never
    looks a track name up except via the wipe/affine transitions this
    function already keeps untouched."""
    referenced_names = set()
    for w in project.wipe_transitions:
        referenced_names.add(w.track_a)
        referenced_names.add(w.track_b)
    for a in project.affine_transitions:
        referenced_names.add(a.track_a)
        referenced_names.add(a.track_b)

    def is_ineligible(t: ResolvedTrack) -> bool:
        return t.name in referenced_names or t.hidden or t.locked or t.audio_muted or t.pan is not None

    video_tracks = [t for t in project.tracks if t.kind == "video"]
    audio_tracks = [t for t in project.tracks if t.kind == "audio"]

    video_ineligible = {i for i, t in enumerate(video_tracks) if is_ineligible(t)}
    audio_ineligible = {i for i, t in enumerate(audio_tracks) if is_ineligible(t)}

    packed_video = _pack_kind(video_tracks, video_ineligible, preserve_order=True)
    packed_audio = _pack_kind(audio_tracks, audio_ineligible, preserve_order=False)

    return replace(project, tracks=[*packed_video, *packed_audio])
