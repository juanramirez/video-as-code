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
        notes=str(data["notes"]) if data.get("notes") is not None else None,
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
    folder = item.get("folder")
    if folder is not None:
        folder = str(folder)

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
            folder=folder,
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
    return BinClipSpec(id=str(item["id"]), path=resolved, type=clip_type, folder=folder)


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

    audio_muted = item.get("audio_muted", False)
    if not isinstance(audio_muted, bool):
        errors.append(msg("validation.audio_muted_invalid", idx=idx, audio_muted=audio_muted))
        audio_muted = False

    return TrackSpec(
        type=track_type, name=name, clips=clips, pan=pan, hidden=hidden, locked=locked, audio_muted=audio_muted
    )


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

    source_in = float(item["source_in"]) if item.get("source_in") is not None else None
    source_out = float(item["source_out"]) if item.get("source_out") is not None else None
    if (source_in is None) != (source_out is None):
        errors.append(msg("validation.source_in_out_incomplete", ctx=ctx))
        source_in = source_out = None
    elif source_in is not None:
        if item.get("in") is not None or item.get("out") is not None or item.get("duration") is not None:
            errors.append(msg("validation.source_in_out_conflicts", ctx=ctx))
            source_in = source_out = None
        elif source_out <= source_in or source_in < 0:
            errors.append(msg("validation.source_in_out_invalid", ctx=ctx, source_in=source_in, source_out=source_out))
            source_in = source_out = None

    speed = float(item["speed"]) if item.get("speed") is not None else None
    if speed == 0:
        errors.append(msg("validation.speed_zero", ctx=ctx))
        speed = None

    speed_ramp_raw = item.get("speed_ramp")
    speed_ramp_image_mode = str(item.get("speed_ramp_image_mode", "nearest"))
    speed_ramp: list[tuple[float, float]] | None = None
    if speed_ramp_raw is not None:
        if speed is not None:
            errors.append(msg("validation.speed_ramp_conflicts_with_speed", ctx=ctx))
        elif not isinstance(speed_ramp_raw, list) or not speed_ramp_raw:
            errors.append(msg("validation.speed_ramp_invalid", ctx=ctx))
        elif speed_ramp_image_mode not in ("nearest", "blend"):
            errors.append(msg("validation.speed_ramp_image_mode_invalid", ctx=ctx, mode=speed_ramp_image_mode))
        else:
            parsed: list[tuple[float, float]] = []
            valid = True
            for i, kf in enumerate(speed_ramp_raw):
                if not isinstance(kf, dict) or "at" not in kf or "speed" not in kf:
                    errors.append(msg("validation.speed_ramp_keyframe_invalid", ctx=ctx, idx=i))
                    valid = False
                    continue
                at = float(kf["at"])
                kf_speed = float(kf["speed"])
                if kf_speed <= 0:
                    errors.append(msg("validation.speed_ramp_speed_invalid", ctx=ctx, idx=i, speed=kf_speed))
                    valid = False
                parsed.append((at, kf_speed))
            if valid and parsed[0][0] != 0:
                errors.append(msg("validation.speed_ramp_must_start_at_zero", ctx=ctx))
                valid = False
            if valid:
                for j in range(1, len(parsed)):
                    if parsed[j][0] <= parsed[j - 1][0]:
                        errors.append(msg("validation.speed_ramp_not_sorted", ctx=ctx))
                        valid = False
                        break
            if valid and item.get("in") not in (None, 0, 0.0):
                errors.append(msg("validation.speed_ramp_in_not_supported", ctx=ctx))
                valid = False
            if valid:
                speed_ramp = parsed

    reveal = item.get("reveal")
    if reveal is not None and reveal not in ("iris", "flip", "glitch", "delaygrab", "cairogradient"):
        errors.append(msg("validation.reveal_invalid", ctx=ctx, reveal=reveal))
        reveal = None

    chroma_key = item.get("chroma_key")
    if chroma_key is not None:
        chroma_key = str(chroma_key)

    chroma_key_distance = float(item.get("chroma_key_distance", 0.35))
    if not (0.0 <= chroma_key_distance <= 1.0):
        errors.append(
            msg("validation.chroma_key_distance_invalid", ctx=ctx, chroma_key_distance=chroma_key_distance)
        )
        chroma_key_distance = 0.35

    overlay_text = item.get("overlay_text")
    overlay_font_size = int(item.get("overlay_font_size", 48))
    if overlay_text is not None and overlay_font_size <= 0:
        errors.append(msg("validation.overlay_font_size_invalid", ctx=ctx, overlay_font_size=overlay_font_size))
        overlay_font_size = 48

    def _quad(key: str) -> tuple[float, float, float, float] | None:
        val = item.get(key)
        if val is None:
            return None
        if not (isinstance(val, (list, tuple)) and len(val) == 4):
            errors.append(msg("validation.quad_invalid", ctx=ctx, key=key))
            return None
        return (float(val[0]), float(val[1]), float(val[2]), float(val[3]))

    ken_burns_from = _quad("ken_burns_from")
    ken_burns_to = _quad("ken_burns_to")
    if (ken_burns_from is None) != (ken_burns_to is None):
        errors.append(msg("validation.ken_burns_incomplete", ctx=ctx))
        ken_burns_from = ken_burns_to = None
    elif ken_burns_from is not None and ken_burns_to is not None:
        if ken_burns_from[2] <= 0 or ken_burns_from[3] <= 0 or ken_burns_to[2] <= 0 or ken_burns_to[3] <= 0:
            errors.append(msg("validation.ken_burns_size_invalid", ctx=ctx))
            ken_burns_from = ken_burns_to = None

    focus_rect = _quad("focus_rect")
    focus_blur = float(item.get("focus_blur", 4.0))
    if focus_rect is not None:
        if focus_rect[2] <= 0 or focus_rect[3] <= 0:
            errors.append(msg("validation.focus_rect_size_invalid", ctx=ctx))
            focus_rect = None
        elif not (0.0 <= focus_blur <= 50.0):
            errors.append(msg("validation.numeric_range_invalid", ctx=ctx, field="focus_blur", min=0, max=50, value=focus_blur))
            focus_blur = 4.0

    position = _pair("position")
    size = _pair("size")

    position_keyframes_raw = item.get("position_keyframes")
    position_keyframes: list[tuple[float, float, float, float, float]] | None = None
    if position_keyframes_raw is not None:
        if position is not None or size is not None:
            errors.append(msg("validation.position_keyframes_conflicts", ctx=ctx))
        elif not isinstance(position_keyframes_raw, list) or not position_keyframes_raw:
            errors.append(msg("validation.position_keyframes_invalid", ctx=ctx))
        else:
            parsed_pk: list[tuple[float, float, float, float, float]] = []
            valid = True
            for i, kf in enumerate(position_keyframes_raw):
                pos = kf.get("position") if isinstance(kf, dict) else None
                sz = kf.get("size") if isinstance(kf, dict) else None
                if (
                    not isinstance(kf, dict) or "at" not in kf
                    or not (isinstance(pos, (list, tuple)) and len(pos) == 2)
                    or not (isinstance(sz, (list, tuple)) and len(sz) == 2)
                ):
                    errors.append(msg("validation.position_keyframes_keyframe_invalid", ctx=ctx, idx=i))
                    valid = False
                    continue
                at = float(kf["at"])
                x, y = float(pos[0]), float(pos[1])
                w, h = float(sz[0]), float(sz[1])
                if w <= 0 or h <= 0:
                    errors.append(msg("validation.pip_size_invalid", ctx=ctx, w=w, h=h))
                    valid = False
                parsed_pk.append((at, x, y, w, h))
            if valid and parsed_pk[0][0] != 0:
                errors.append(msg("validation.position_keyframes_must_start_at_zero", ctx=ctx))
                valid = False
            if valid:
                for j in range(1, len(parsed_pk)):
                    if parsed_pk[j][0] <= parsed_pk[j - 1][0]:
                        errors.append(msg("validation.position_keyframes_not_sorted", ctx=ctx))
                        valid = False
                        break
            if valid:
                position_keyframes = parsed_pk

    crop_raw = item.get("crop")
    crop: tuple[float, float, float, float] | None = None
    if crop_raw is not None:
        if position is not None or size is not None or position_keyframes is not None:
            errors.append(msg("validation.crop_conflicts", ctx=ctx))
        elif not isinstance(crop_raw, dict):
            errors.append(msg("validation.crop_invalid", ctx=ctx))
        else:
            top = float(crop_raw.get("top", 0))
            right = float(crop_raw.get("right", 0))
            bottom = float(crop_raw.get("bottom", 0))
            left = float(crop_raw.get("left", 0))
            if top < 0 or right < 0 or bottom < 0 or left < 0:
                errors.append(msg("validation.crop_negative", ctx=ctx))
            else:
                crop = (top, right, bottom, left)

    freeze_at = float(item["freeze_at"]) if item.get("freeze_at") is not None else None
    freeze_before = bool(item.get("freeze_before", False))
    freeze_after = bool(item.get("freeze_after", False))
    if freeze_at is None and (freeze_before or freeze_after):
        errors.append(msg("validation.freeze_before_after_without_at", ctx=ctx))
        freeze_before = freeze_after = False
    elif freeze_at is not None and freeze_at < 0:
        errors.append(msg("validation.freeze_at_negative", ctx=ctx, freeze_at=freeze_at))
        freeze_at = None

    old_film_flags = {}
    for field_name in ("old_film", "film_dust", "film_scratches", "film_grain"):
        raw = item.get(field_name)
        if raw is None:
            old_film_flags[field_name] = None
        elif not isinstance(raw, bool):
            errors.append(msg("validation.bool_field_invalid", ctx=ctx, field=field_name, value=raw))
            old_film_flags[field_name] = None
        else:
            old_film_flags[field_name] = raw

    color_grade_raw = item.get("color_grade")
    color_grade: dict[str, tuple[float, float, float]] | None = None
    if color_grade_raw is not None:
        if not isinstance(color_grade_raw, dict) or not color_grade_raw:
            errors.append(msg("validation.color_grade_invalid", ctx=ctx))
        else:
            valid = True
            parsed_cg: dict[str, tuple[float, float, float]] = {}
            for key, val in color_grade_raw.items():
                if key not in ("lift", "gamma", "gain"):
                    errors.append(msg("validation.color_grade_unknown_group", ctx=ctx, group=key))
                    valid = False
                    continue
                if not (isinstance(val, (list, tuple)) and len(val) == 3):
                    errors.append(msg("validation.color_grade_triple_invalid", ctx=ctx, group=key))
                    valid = False
                    continue
                r, g, b = float(val[0]), float(val[1]), float(val[2])
                if r < 0 or g < 0 or b < 0:
                    errors.append(msg("validation.color_grade_negative", ctx=ctx, group=key))
                    valid = False
                    continue
                parsed_cg[key] = (r, g, b)
            if valid:
                color_grade = parsed_cg

    return ClipPlacementSpec(
        clip=str(item["clip"]),
        start=float(item["start"]),
        in_=float(item.get("in", 0.0)),
        out=float(item["out"]) if "out" in item and item["out"] is not None else None,
        duration=float(item["duration"]) if "duration" in item and item["duration"] is not None else None,
        source_in=source_in,
        source_out=source_out,
        fade_in=float(item["fade_in"]) if item.get("fade_in") is not None else None,
        fade_out=float(item["fade_out"]) if item.get("fade_out") is not None else None,
        mute=mute,
        speed=speed,
        preserve_pitch=bool(item.get("preserve_pitch", False)),
        speed_ramp=speed_ramp,
        speed_ramp_image_mode=speed_ramp_image_mode,
        position=position,
        size=size,
        position_keyframes=position_keyframes,
        crop=crop,
        freeze_at=freeze_at,
        freeze_before=freeze_before,
        freeze_after=freeze_after,
        reveal_style=reveal,
        chroma_key=chroma_key,
        chroma_key_distance=chroma_key_distance,
        overlay_text=str(overlay_text) if overlay_text is not None else None,
        overlay_font=str(item.get("overlay_font", "Sans")),
        overlay_font_size=overlay_font_size,
        overlay_color=tuple(item.get("overlay_color", (255, 255, 255, 255))),
        overlay_background=tuple(item.get("overlay_background", (0, 0, 0, 0))),
        ken_burns_from=ken_burns_from,
        ken_burns_to=ken_burns_to,
        focus_rect=focus_rect,
        focus_blur=focus_blur,
        old_film=old_film_flags["old_film"],
        film_dust=old_film_flags["film_dust"],
        film_scratches=old_film_flags["film_scratches"],
        film_grain=old_film_flags["film_grain"],
        color_grade=color_grade,
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
    titles_dir = doc.source_path.parent / ".titles"
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
            folder=clip.folder,
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

            speed_ramp = placement.speed_ramp
            if speed_ramp is not None and bin_clip.kind in ("image", "title"):
                errors.append(msg("validation.speed_ramp_on_image", ctx=ctx, clip=placement.clip))
                speed_ramp = None

            if speed_ramp is not None:
                # Note: `source_in`/`source_out` combined with `speed_ramp`
                # is already rejected earlier - speed_ramp requires an
                # explicit out:/duration: (below), and source_in/
                # source_out reject being combined with in:/out:/
                # duration: (see the source_in_out_conflicts check), so
                # the two are mutually exclusive by construction, no
                # separate check needed here.
                # Deliberately narrow v0.7 scope (see
                # ClipPlacementSpec.speed_ramp): always starts at the
                # source's own beginning (in_s forced to 0 - already
                # validated in _parse_clip_placement that `in:` isn't
                # set alongside a ramp), and needs an explicit
                # out:/duration: - no auto-inferred "use all available
                # source" default like flat `speed:` gets, since that
                # would need solving for where a multi-segment ramp
                # runs out of source material rather than a single
                # division.
                in_s = 0.0
                if placement.out is not None:
                    out_s = placement.out
                elif placement.duration is not None:
                    out_s = placement.duration
                else:
                    errors.append(msg("validation.speed_ramp_requires_duration", ctx=ctx))
                    out_s = max((at for at, _ in speed_ramp), default=0.0) + 1.0
                for at, _ in speed_ramp:
                    if at >= out_s:
                        errors.append(
                            msg("validation.speed_ramp_breakpoint_out_of_range", ctx=ctx, at=at, out_s=out_s)
                        )
                source_consumed_s = 0.0
                for i, (at, kf_speed) in enumerate(speed_ramp):
                    segment_end_s = speed_ramp[i + 1][0] if i + 1 < len(speed_ramp) else out_s
                    source_consumed_s += max(segment_end_s - at, 0.0) * kf_speed
                available_source_s = fps.frames_to_seconds(bin_clip.duration_frames)
                if source_consumed_s > available_source_s + 1e-6:
                    errors.append(
                        msg(
                            "validation.speed_ramp_exceeds_source",
                            ctx=ctx,
                            consumed_s=round(source_consumed_s, 3),
                            available_s=round(available_source_s, 3),
                        )
                    )
                # The ramp's own bound checks above already cover what
                # the shared out_exceeds_duration check below would -
                # setting source_duration_s = out_s makes that check a
                # deliberate no-op rather than duplicating it.
                source_duration_s = out_s
            else:
                # `speed` shortens (or stretches) the clip's *usable*
                # duration on the timeline - in/out/duration below are
                # relative to that warped length, matching how kdenlive
                # itself works (and what xmlgen.py's timewarp producer
                # actually exposes as its own [0, warped_duration)
                # range). A negative speed (reverse playback) still
                # warps the duration by its magnitude - only the
                # direction flips, not the length.
                effective_duration_frames = (
                    round(bin_clip.duration_frames / abs(speed)) if speed else bin_clip.duration_frames
                )
                source_duration_s = fps.frames_to_seconds(effective_duration_frames)
                if placement.source_in is not None:
                    # Always real source seconds regardless of `speed:` -
                    # divide by it here so the caller doesn't have to.
                    # The usual in_s<0/out_s>source_duration_s checks
                    # below still apply to the *converted* (warped)
                    # values, so an out-of-range source_out is still
                    # caught, just against the warped bound like any
                    # other out_s would be.
                    divisor = abs(speed) if speed else 1.0
                    in_s = placement.source_in / divisor
                    out_s = placement.source_out / divisor
                else:
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

            position_keyframes = placement.position_keyframes
            if position_keyframes is not None:
                for i, (_at, x, y, w, h) in enumerate(position_keyframes):
                    if x < 0 or y < 0 or x + w > doc.project.width + 1e-6 or y + h > doc.project.height + 1e-6:
                        errors.append(
                            msg(
                                "validation.position_keyframes_out_of_bounds",
                                ctx=ctx,
                                idx=i,
                                x=x, y=y, w=w, h=h,
                                width=doc.project.width, height=doc.project.height,
                            )
                        )

            freeze_at_frame = fps.seconds_to_frames(placement.freeze_at) if placement.freeze_at is not None else None
            if freeze_at_frame is not None:
                clip_length_frames = fps.seconds_to_frames(out_s) - fps.seconds_to_frames(in_s)
                if freeze_at_frame < 0 or freeze_at_frame >= clip_length_frames:
                    errors.append(
                        msg(
                            "validation.freeze_at_out_of_bounds",
                            ctx=ctx,
                            freeze_at=placement.freeze_at,
                            duration=fps.frames_to_seconds(clip_length_frames),
                        )
                    )

            if errors:
                continue

            in_frame = fps.seconds_to_frames(in_s)
            out_frame = fps.seconds_to_frames(out_s) - 1  # last included frame

            speed_ramp_frames = (
                [(fps.seconds_to_frames(at), kf_speed) for at, kf_speed in speed_ramp]
                if speed_ramp is not None
                else None
            )
            position_keyframes_frames = (
                [(fps.seconds_to_frames(at), x, y, w, h) for at, x, y, w, h in position_keyframes]
                if position_keyframes is not None
                else None
            )

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
                    speed_ramp=speed_ramp_frames,
                    speed_ramp_image_mode=placement.speed_ramp_image_mode,
                    rect=rect,
                    position_keyframes=position_keyframes_frames,
                    crop=placement.crop,
                    freeze_at_frame=freeze_at_frame,
                    freeze_before=placement.freeze_before,
                    freeze_after=placement.freeze_after,
                    reveal_style=placement.reveal_style,
                    chroma_key=placement.chroma_key,
                    chroma_key_distance=placement.chroma_key_distance,
                    overlay_text=placement.overlay_text,
                    overlay_font=placement.overlay_font,
                    overlay_font_size=placement.overlay_font_size,
                    overlay_color=placement.overlay_color,
                    overlay_background=placement.overlay_background,
                    ken_burns_from=placement.ken_burns_from,
                    ken_burns_to=placement.ken_burns_to,
                    focus_rect=placement.focus_rect,
                    focus_blur=placement.focus_blur,
                    color_grade=placement.color_grade,
                    old_film=placement.old_film,
                    film_dust=placement.film_dust,
                    film_scratches=placement.film_scratches,
                    film_grain=placement.film_grain,
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
                audio_muted=track.audio_muted,
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
        notes=doc.project.notes,
    )
