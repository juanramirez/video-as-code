"""Loading the declarative YAML and full semantic validation.

Philosophy: accumulate every error found and report them all at once
(`ValidationErrors`), instead of aborting on the first one. It checks:

  - that the YAML has the expected shape (required keys, types)
  - that every referenced asset exists on disk
  - that each clip's `in`/`out`/`duration` are consistent with the source
    file's real duration (via ffprobe)
  - that clips don't accidentally overlap within the same track
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import probe, titles
from .errors import ValidationErrors
from .i18n import msg
from .lumas import AVAILABLE_LUMAS
from .models import (
    AFFINE_STYLES,
    AffineTransitionSpec,
    BinClipSpec,
    ClipPlacementSpec,
    GuideSpec,
    ProjectDoc,
    ProjectSpec,
    ResolvedAffineTransition,
    ResolvedBinClip,
    ResolvedGuide,
    ResolvedPlacement,
    ResolvedProject,
    ResolvedTrack,
    ResolvedWipeTransition,
    TrackSpec,
    VALID_CLIP_TYPES,
    VALID_TRACK_TYPES,
    WipeTransitionSpec,
)
from .timecode import FrameRate, resolve_fps

RESERVED_TOP_LEVEL_KEYS = {
    "project", "bin", "timeline", "scenes", "guides", "transitions",
    "x_kdenlive_raw", "x_reconstruct_unmatched",
}


def load_yaml(path: str | Path) -> ProjectDoc:
    path = Path(path).resolve()
    if not path.exists():
        raise ValidationErrors([msg("validation.yaml_missing", path=path)])

    with path.open("r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ValidationErrors([msg("validation.yaml_invalid", path=path, exc=exc)]) from exc

    errors: list[str] = []
    source_dir = path.parent

    project_data = data.get("project")
    if not isinstance(project_data, dict):
        raise ValidationErrors([msg("validation.project_section_missing", path=path)])

    project = _parse_project(project_data, errors)

    bin_data = data.get("bin", [])
    if not isinstance(bin_data, list):
        errors.append(msg("validation.bin_not_list"))
        bin_data = []
    bin_clips = [c for c in (_parse_bin_clip(item, source_dir, errors) for item in bin_data) if c]

    timeline_data = data.get("timeline", {})
    tracks_data = timeline_data.get("tracks", []) if isinstance(timeline_data, dict) else []
    if not isinstance(tracks_data, list):
        errors.append(msg("validation.tracks_not_list"))
        tracks_data = []
    tracks = [t for t in (_parse_track(item, idx, errors) for idx, item in enumerate(tracks_data)) if t]

    unknown_keys = set(data.keys()) - RESERVED_TOP_LEVEL_KEYS
    if unknown_keys:
        errors.append(msg("validation.unknown_top_level_keys", keys=", ".join(sorted(unknown_keys))))

    scenes_data = data.get("scenes", [])
    if not isinstance(scenes_data, list):
        errors.append(msg("validation.scenes_not_list"))
        scenes_data = []

    guides_data = data.get("guides", [])
    if not isinstance(guides_data, list):
        errors.append(msg("validation.guides_not_list"))
        guides_data = []
    guides = [g for g in (_parse_guide(item, idx, errors) for idx, item in enumerate(guides_data)) if g]

    transitions_data = data.get("transitions", [])
    if not isinstance(transitions_data, list):
        errors.append(msg("validation.transitions_not_list"))
        transitions_data = []
    track_names = {t.name for t in tracks}
    wipe_transitions: list[WipeTransitionSpec] = []
    affine_transitions: list[AffineTransitionSpec] = []
    for idx, item in enumerate(transitions_data):
        wipe, affine = _parse_transition(item, idx, track_names, errors)
        if wipe is not None:
            wipe_transitions.append(wipe)
        if affine is not None:
            affine_transitions.append(affine)

    if errors:
        raise ValidationErrors(errors)

    return ProjectDoc(
        project=project,
        bin=bin_clips,
        tracks=tracks,
        source_path=path,
        raw_extras=data.get("x_kdenlive_raw"),
        scenes_raw=scenes_data,
        guides=guides,
        wipe_transitions=wipe_transitions,
        affine_transitions=affine_transitions,
    )


def load_and_resolve(path: str | Path) -> ResolvedProject:
    """High-level entry point: loads the YAML, compiles `scenes:` (if any)
    into explicit tracks/clips, and validates+resolves the result."""
    from . import scenes as scenes_mod  # deferred import: avoids a cycle with schema

    doc = load_yaml(path)
    if doc.scenes_raw:
        errors: list[str] = []
        doc = scenes_mod.compile_scenes(doc, doc.scenes_raw, errors)
        if errors:
            raise ValidationErrors(errors)
    return validate_and_resolve(doc)


def _parse_project(data: dict, errors: list[str]) -> ProjectSpec:
    required = ["id", "title", "fps", "width", "height"]
    for key in required:
        if key not in data:
            errors.append(msg("validation.project_field_required", key=key))
    if errors:
        # Can't build a ProjectSpec without the bare minimum.
        return ProjectSpec(
            id=str(data.get("id", "proyecto")),
            title=str(data.get("title", "")),
            fps=float(data.get("fps", 25)),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
        )
    colorspace = int(data.get("colorspace", 709))
    if colorspace <= 0:
        errors.append(msg("validation.colorspace_invalid", colorspace=colorspace))
    return ProjectSpec(
        id=str(data["id"]),
        title=str(data["title"]),
        fps=float(data["fps"]),
        width=int(data["width"]),
        height=int(data["height"]),
        colorspace=colorspace,
        default_image_duration=float(data.get("default_image_duration", 4.0)),
    )


def _parse_bin_clip(item: Any, source_dir: Path, errors: list[str]) -> BinClipSpec | None:
    if not isinstance(item, dict) or "id" not in item:
        errors.append(msg("validation.bin_entry_invalid", item=item))
        return None
    clip_type = item.get("type")
    if clip_type is not None and clip_type not in VALID_CLIP_TYPES:
        errors.append(
            msg("validation.bin_type_invalid", id=item.get("id"), type=clip_type, valid=VALID_CLIP_TYPES)
        )

    if clip_type == "title":
        # No `path`: schema.py generates the .kdenlivetitle file itself
        # (see the bin-probing loop in validate_and_resolve) and fills
        # this in once it knows where.
        text = item.get("text")
        if not text:
            errors.append(msg("validation.title_missing_text", id=item.get("id")))
            return None
        font_size = int(item.get("font_size", 48))
        if font_size <= 0:
            errors.append(msg("validation.title_font_size_invalid", id=item.get("id"), font_size=font_size))
            return None
        return BinClipSpec(
            id=str(item["id"]),
            path=None,
            type=clip_type,
            text=str(text),
            font=str(item.get("font", "Sans")),
            font_size=font_size,
            font_color=tuple(item.get("color", (255, 255, 255, 255))),
            background=tuple(item.get("background", (0, 0, 0, 0))),
        )

    if "path" not in item:
        errors.append(msg("validation.bin_entry_invalid", item=item))
        return None
    raw_path = Path(item["path"])
    resolved = raw_path if raw_path.is_absolute() else (source_dir / raw_path)
    return BinClipSpec(id=str(item["id"]), path=resolved, type=clip_type)


def _parse_track(item: Any, idx: int, errors: list[str]) -> TrackSpec | None:
    if not isinstance(item, dict) or "type" not in item:
        errors.append(msg("validation.track_missing_type", idx=idx, item=item))
        return None
    track_type = item["type"]
    if track_type not in VALID_TRACK_TYPES:
        errors.append(msg("validation.track_type_invalid", idx=idx, type=track_type, valid=VALID_TRACK_TYPES))
        return None
    name = item.get("name") or f"{'V' if track_type == 'video' else 'A'}{idx + 1}"
    clips_data = item.get("clips", [])
    if not isinstance(clips_data, list):
        errors.append(msg("validation.tracks_clips_not_list", idx=idx))
        clips_data = []
    clips = [
        c for c in (_parse_clip_placement(c, idx, i, errors) for i, c in enumerate(clips_data)) if c
    ]

    pan = float(item["pan"]) if item.get("pan") is not None else None
    if pan is not None:
        if track_type != "audio":
            errors.append(msg("validation.pan_on_video_track", idx=idx))
            pan = None
        elif not (-1.0 <= pan <= 1.0):
            errors.append(msg("validation.pan_out_of_range", idx=idx, pan=pan))
            pan = None

    hidden = item.get("hidden", False)
    if not isinstance(hidden, bool):
        errors.append(msg("validation.hidden_invalid", idx=idx, hidden=hidden))
        hidden = False

    locked = item.get("locked", False)
    if not isinstance(locked, bool):
        errors.append(msg("validation.locked_invalid", idx=idx, locked=locked))
        locked = False

    return TrackSpec(type=track_type, name=name, clips=clips, pan=pan, hidden=hidden, locked=locked)


def _parse_clip_placement(item: Any, track_idx: int, clip_idx: int, errors: list[str]) -> ClipPlacementSpec | None:
    ctx = f"timeline.tracks[{track_idx}].clips[{clip_idx}]"
    if not isinstance(item, dict) or "clip" not in item or "start" not in item:
        errors.append(msg("validation.clip_missing_fields", ctx=ctx, item=item))
        return None

    def _pair(key: str) -> tuple[float, float] | None:
        val = item.get(key)
        if val is None:
            return None
        if not (isinstance(val, (list, tuple)) and len(val) == 2):
            errors.append(msg("validation.pair_invalid", ctx=ctx, key=key))
            return None
        return (float(val[0]), float(val[1]))

    mute = item.get("mute", False)
    if not isinstance(mute, bool):
        errors.append(msg("validation.mute_invalid", ctx=ctx, mute=mute))
        mute = False

    speed = float(item["speed"]) if item.get("speed") is not None else None
    if speed == 0:
        errors.append(msg("validation.speed_zero", ctx=ctx))
        speed = None

    return ClipPlacementSpec(
        clip=str(item["clip"]),
        start=float(item["start"]),
        in_=float(item.get("in", 0.0)),
        out=float(item["out"]) if "out" in item and item["out"] is not None else None,
        duration=float(item["duration"]) if "duration" in item and item["duration"] is not None else None,
        fade_in=float(item["fade_in"]) if item.get("fade_in") is not None else None,
        fade_out=float(item["fade_out"]) if item.get("fade_out") is not None else None,
        mute=mute,
        speed=speed,
        preserve_pitch=bool(item.get("preserve_pitch", False)),
        position=_pair("position"),
        size=_pair("size"),
    )


def _parse_guide(item: Any, idx: int, errors: list[str]) -> GuideSpec | None:
    ctx = f"guides[{idx}]"
    if not isinstance(item, dict) or "at" not in item:
        errors.append(msg("validation.guide_missing_at", ctx=ctx, item=item))
        return None
    at = float(item["at"])
    if at < 0:
        errors.append(msg("validation.guide_at_negative", ctx=ctx, at=at))
        return None
    return GuideSpec(
        at=at,
        text=str(item.get("text", "Marker")),
        category=int(item.get("category", 0)),
    )


def _parse_transition(
    item: Any, idx: int, track_names: set[str], errors: list[str]
) -> tuple[WipeTransitionSpec | None, AffineTransitionSpec | None]:
    """A top-level `transitions:` entry - the low-level, `scenes:`-free way
    to declare a cross-track `wipe`/`affine` transition directly between
    two `timeline.tracks:` (by name). This is what `reverse.py` emits for
    a wipe/affine `<transition>` it recognizes on import: reconstructing
    the original `scenes:` sugar (clip list, pacing) isn't attempted, but
    the transition itself is no longer silently lost to `x_kdenlive_raw`
    either - see ROADMAP.md's v0.4 entry."""
    ctx = f"transitions[{idx}]"
    if not isinstance(item, dict) or "type" not in item:
        errors.append(msg("validation.transition_missing_type", ctx=ctx, item=item))
        return None, None
    t_type = item["type"]
    if t_type not in ("wipe", "affine"):
        errors.append(msg("validation.transition_type_invalid", ctx=ctx, type=t_type))
        return None, None

    track_a, track_b = item.get("track_a"), item.get("track_b")
    if track_a not in track_names or track_b not in track_names:
        errors.append(msg("validation.transition_unknown_track", ctx=ctx, track_a=track_a, track_b=track_b))
        return None, None

    if "start" not in item or "end" not in item:
        errors.append(msg("validation.transition_missing_span", ctx=ctx))
        return None, None
    start, end = float(item["start"]), float(item["end"])
    if end <= start:
        errors.append(msg("validation.transition_end_not_after_start", ctx=ctx, start=start, end=end))
        return None, None

    if t_type == "wipe":
        luma = item.get("luma", "linear_x")
        if luma not in AVAILABLE_LUMAS:
            errors.append(msg("validation.transition_luma_invalid", ctx=ctx, luma=luma, valid=list(AVAILABLE_LUMAS)))
            return None, None
        return WipeTransitionSpec(track_a=track_a, track_b=track_b, start=start, end=end, luma=luma), None

    style = item.get("style", "zoom")
    if style not in AFFINE_STYLES:
        errors.append(msg("validation.transition_style_invalid", ctx=ctx, style=style, valid=list(AFFINE_STYLES)))
        return None, None
    return None, AffineTransitionSpec(track_a=track_a, track_b=track_b, start=start, end=end, style=style)


def validate_and_resolve(doc: ProjectDoc) -> ResolvedProject:
    errors: list[str] = []
    fps = resolve_fps(doc.project.fps)

    # --- assets: existence on disk -----------------------------------------
    bin_by_id: dict[str, BinClipSpec] = {}
    for clip in doc.bin:
        if clip.id in bin_by_id:
            errors.append(msg("validation.bin_duplicate_id", id=clip.id))
            continue
        bin_by_id[clip.id] = clip
        if clip.type != "title" and not clip.path.exists():
            errors.append(msg("validation.bin_missing_file", id=clip.id, path=clip.path))

    if errors:
        # No point continuing without valid assets (probing would fail in a chain).
        raise ValidationErrors(errors)

    # --- probing: source type and duration ----------------------------------
    titles_dir = doc.source_path.parent / ".kdgen_titles"
    resolved_bin: dict[str, ResolvedBinClip] = {}
    for numeric_id, (clip_id, clip) in enumerate(bin_by_id.items(), start=1):
        kind = clip.type or probe.guess_type(clip.path)
        if kind == "title":
            # Generated content, not a pre-existing asset: no duration to
            # probe (same "however long you like" flexibility as an
            # image - eof=pause holds the last frame either way).
            duration_seconds = doc.project.default_image_duration
            clip.path = titles_dir / f"{clip_id}.kdenlivetitle"
            titles.write_title_file(
                clip.path,
                titles.render_title_xml(
                    clip.text,
                    width=doc.project.width,
                    height=doc.project.height,
                    duration_frames=fps.seconds_to_frames(duration_seconds),
                    font=clip.font,
                    font_size=clip.font_size,
                    font_color=clip.font_color,
                    background=clip.background,
                ),
            )
        elif kind == "image":
            duration_seconds = doc.project.default_image_duration
        else:
            try:
                duration_seconds = probe.probe_duration_seconds(str(clip.path))
            except probe.ProbeError as exc:
                errors.append(str(exc))
                continue
        resolved_bin[clip_id] = ResolvedBinClip(
            id=clip_id,
            numeric_id=numeric_id,
            path=clip.path,
            kind=kind,
            duration_frames=fps.seconds_to_frames(duration_seconds),
        )

    if errors:
        raise ValidationErrors(errors)

    # --- tracks: resolve in/out/duration and check for overlaps ------------
    resolved_tracks: list[ResolvedTrack] = []
    for t_idx, track in enumerate(doc.tracks):
        placements: list[ResolvedPlacement] = []
        for c_idx, placement in enumerate(track.clips):
            ctx = f"timeline.tracks[{t_idx}]({track.name}).clips[{c_idx}]"
            bin_clip = resolved_bin.get(placement.clip)
            if bin_clip is None:
                errors.append(msg("validation.clip_unknown_reference", ctx=ctx, clip=placement.clip))
                continue

            speed = placement.speed
            if speed is not None and bin_clip.kind in ("image", "title"):
                errors.append(msg("validation.speed_on_image", ctx=ctx, clip=placement.clip))
                speed = None

            # `speed` shortens (or stretches) the clip's *usable* duration
            # on the timeline - in/out/duration below are relative to that
            # warped length, matching how kdenlive itself works (and what
            # xmlgen.py's timewarp producer actually exposes as its own
            # [0, warped_duration) range). A negative speed (reverse
            # playback) still warps the duration by its magnitude - only
            # the direction flips, not the length.
            effective_duration_frames = (
                round(bin_clip.duration_frames / abs(speed)) if speed else bin_clip.duration_frames
            )
            source_duration_s = fps.frames_to_seconds(effective_duration_frames)
            in_s = placement.in_
            if placement.out is not None:
                out_s = placement.out
            elif placement.duration is not None:
                out_s = in_s + placement.duration
            else:
                out_s = source_duration_s

            if in_s < 0:
                errors.append(msg("validation.in_negative", ctx=ctx, in_s=in_s))
            if out_s <= in_s:
                errors.append(msg("validation.out_not_greater_than_in", ctx=ctx, out_s=out_s, in_s=in_s))
            if bin_clip.kind not in ("image", "title") and out_s > source_duration_s + 1e-6:
                errors.append(
                    msg(
                        "validation.out_exceeds_duration",
                        ctx=ctx,
                        out_s=out_s,
                        clip=placement.clip,
                        source_s=source_duration_s,
                    )
                )
            if placement.start < 0:
                errors.append(msg("validation.start_negative", ctx=ctx, start=placement.start))

            clip_len_s = max(out_s - in_s, 0.0)
            fade_in_s = placement.fade_in or 0.0
            fade_out_s = placement.fade_out or 0.0
            if fade_in_s < 0 or fade_out_s < 0:
                errors.append(msg("validation.fade_negative", ctx=ctx))
            elif fade_in_s + fade_out_s > clip_len_s + 1e-6:
                errors.append(
                    msg(
                        "validation.fade_exceeds_duration",
                        ctx=ctx,
                        fade_in=fade_in_s,
                        fade_out=fade_out_s,
                        clip_len=clip_len_s,
                    )
                )

            rect = None
            if placement.position is not None or placement.size is not None:
                x, y = placement.position or (0.0, 0.0)
                w, h = placement.size or (float(doc.project.width), float(doc.project.height))
                if w <= 0 or h <= 0:
                    errors.append(msg("validation.pip_size_invalid", ctx=ctx, w=w, h=h))
                elif x < 0 or y < 0 or x + w > doc.project.width + 1e-6 or y + h > doc.project.height + 1e-6:
                    errors.append(
                        msg(
                            "validation.pip_out_of_bounds",
                            ctx=ctx,
                            x=x, y=y, w=w, h=h,
                            width=doc.project.width, height=doc.project.height,
                        )
                    )
                rect = (x, y, w, h)

            if errors:
                continue

            in_frame = fps.seconds_to_frames(in_s)
            out_frame = fps.seconds_to_frames(out_s) - 1  # last included frame

            placements.append(
                ResolvedPlacement(
                    bin_clip=bin_clip,
                    start_frame=fps.seconds_to_frames(placement.start),
                    in_frame=in_frame,
                    out_frame=max(out_frame, in_frame),
                    fade_in_frames=fps.seconds_to_frames(fade_in_s),
                    fade_out_frames=fps.seconds_to_frames(fade_out_s),
                    mute=placement.mute,
                    speed=speed,
                    preserve_pitch=placement.preserve_pitch,
                    rect=rect,
                    reveal_style=placement.reveal_style,
                )
            )

        placements.sort(key=lambda p: p.start_frame)
        for prev, nxt in zip(placements, placements[1:]):
            prev_end = prev.start_frame + prev.length_frames
            if nxt.start_frame < prev_end:
                errors.append(
                    msg(
                        "validation.clip_overlap",
                        idx=t_idx,
                        name=track.name,
                        prev=prev.bin_clip.id,
                        prev_end=prev_end,
                        next=nxt.bin_clip.id,
                        next_start=nxt.start_frame,
                    )
                )

        resolved_tracks.append(
            ResolvedTrack(
                kind=track.type,
                name=track.name,
                placements=placements,
                pan=track.pan,
                hidden=track.hidden,
                locked=track.locked,
            )
        )

    if errors:
        raise ValidationErrors(errors)

    resolved_wipes = [
        ResolvedWipeTransition(
            track_a=w.track_a,
            track_b=w.track_b,
            start_frame=fps.seconds_to_frames(w.start),
            end_frame=fps.seconds_to_frames(w.end) - 1,
            luma=w.luma,
        )
        for w in doc.wipe_transitions
    ]
    resolved_affines = [
        ResolvedAffineTransition(
            track_a=a.track_a,
            track_b=a.track_b,
            start_frame=fps.seconds_to_frames(a.start),
            end_frame=fps.seconds_to_frames(a.end) - 1,
            style=a.style,
        )
        for a in doc.affine_transitions
    ]
    resolved_guides = [
        ResolvedGuide(frame=fps.seconds_to_frames(g.at), text=g.text, category=g.category)
        for g in doc.guides
    ]

    return ResolvedProject(
        project_id=doc.project.id,
        title=doc.project.title,
        fps=fps,
        width=doc.project.width,
        height=doc.project.height,
        colorspace=doc.project.colorspace,
        bin_clips=list(resolved_bin.values()),
        tracks=resolved_tracks,
        source_dir=doc.source_path.parent,
        wipe_transitions=resolved_wipes,
        affine_transitions=resolved_affines,
        guides=resolved_guides,
    )
