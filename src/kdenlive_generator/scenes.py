"""Compiles the high-level `scenes:` YAML into explicit tracks/clips of the
low-level model (`timeline.tracks`), which is what `schema.validate_and_resolve`
validates and resolves.

A "scene" is a list of clips that play one after another. Its duration is
computed automatically (via `pacing`, or by splitting a total `duration`
across the clips) instead of having to set `start`/`duration` by hand on
each one. It's syntactic sugar: everything is translated into regular
`BinClipSpec` + `TrackSpec` *before* validation, so any overlap or duration
problem is caught by the same checks the rest of the pipeline uses.

## `transition: dissolve` — how it works (best-effort)

In MLT, two clips can't overlap within the same playlist. To get a real
dissolve (two clips visible at once, cross-fading) you need two separate
video tracks that overlap in time. So as soon as a scene asks for
`dissolve`, the compiler splits its clips across two alternating tracks
("Scenes A" / "Scenes B", shared by every scene in the document) with a
`transition_duration`-second overlap between consecutive clips.

The fade itself reuses the fade to/from black filter (`brightness` with
`alpha` keyframes) already used by `fade_in`/`fade_out`: an alpha of 0 on an
upper track doesn't paint black, it lets the track below show through, so it
works as a cross-fade. Only the clip that ends up on the "top" track in each
transition needs to carry the fade (the other stays opaque throughout: it's
revealed or covered solely by the alpha of the one on top).

Verified against a real `melt` render (not just structurally): a red clip
dissolving into a blue one produces a genuine, monotonic cross-fade
through the transition window, not a hard cut or a fade through black
(`tests/test_melt_validation.py::test_scenes_dissolve_produces_a_real_crossfade`).
That test needs a `melt` build with a working `qtblend` - dissolve is
inherently cross-track alpha compositing, so there's no way around that
dependency the way other tests avoid it (see conftest.py's
`melt_with_qtblend`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import probe
from .i18n import msg
from .models import BinClipSpec, ClipPlacementSpec, ProjectDoc, TrackSpec

# Seconds per clip based on the scene's "pacing", if no `duration` is given.
# image: fixed duration. video_cap: upper bound (if the source clip is
# shorter than the cap, it's used whole, never stretched).
PACING_PRESETS = {
    "energetic": {"image": 1.5, "video_cap": 3.0},
    "normal": {"image": 3.0, "video_cap": 6.0},
    "slow": {"image": 5.0, "video_cap": 10.0},
}
DEFAULT_TRANSITION_DURATION = 1.0
SCENE_TRACK_NAMES = (msg("scenes.track_a"), msg("scenes.track_b"))


def compile_scenes(doc: ProjectDoc, scenes_data: list, errors: list[str]) -> ProjectDoc:
    if not scenes_data:
        return doc

    source_dir = doc.source_path.parent
    bin_by_id: dict[str, BinClipSpec] = {c.id: c for c in doc.bin}
    bin_by_path: dict[Path, BinClipSpec] = {c.path: c for c in doc.bin}
    new_bin: list[BinClipSpec] = list(doc.bin)

    def register_clip(raw: Any, ctx: str, idx: int) -> Optional[str]:
        """Accepts a path ('beach_01.jpeg') or {'clip': id, ...} referencing
        an id already defined in `bin:`. Paths are auto-registered in the bin
        (id = filename without extension, disambiguated if needed)."""
        if isinstance(raw, dict) and "clip" in raw:
            clip_id = str(raw["clip"])
            if clip_id not in bin_by_id:
                errors.append(msg("validation.scene_clip_undefined", ctx=ctx, idx=idx, clip_id=clip_id))
                return None
            return clip_id
        if not isinstance(raw, str):
            errors.append(msg("validation.scene_clip_invalid", ctx=ctx, idx=idx, raw=raw))
            return None

        candidate = (source_dir / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if candidate in bin_by_path:
            return bin_by_path[candidate].id

        clip_id = base_id = Path(raw).stem
        n = 2
        while clip_id in bin_by_id:
            clip_id = f"{base_id}_{n}"
            n += 1
        spec = BinClipSpec(id=clip_id, path=candidate)
        new_bin.append(spec)
        bin_by_id[clip_id] = spec
        bin_by_path[candidate] = spec
        return clip_id

    track_clips: tuple[list[ClipPlacementSpec], list[ClipPlacementSpec]] = ([], [])
    cursor = 0.0  # global second at which the scenes block starts

    for s_idx, scene in enumerate(scenes_data):
        if not isinstance(scene, dict) or "clips" not in scene:
            errors.append(msg("validation.scene_missing_clips", idx=s_idx, scene=scene))
            continue
        name = scene.get("scene") or f"scene{s_idx}"
        ctx = f"scenes[{s_idx}]({name})"
        raw_clips = scene["clips"]
        if not isinstance(raw_clips, list) or not raw_clips:
            errors.append(msg("validation.scene_clips_not_list", ctx=ctx))
            continue

        transition = scene.get("transition", "none")
        if transition not in ("none", "dissolve"):
            errors.append(msg("validation.scene_transition_invalid", ctx=ctx, transition=transition))
            continue
        overlap = float(scene.get("transition_duration", DEFAULT_TRANSITION_DURATION)) if transition == "dissolve" else 0.0

        pacing = scene.get("pacing", "normal")
        if pacing not in PACING_PRESETS:
            errors.append(msg("validation.scene_pacing_invalid", ctx=ctx, pacing=pacing, valid=list(PACING_PRESETS)))
            continue
        preset = PACING_PRESETS[pacing]

        clip_ids = [register_clip(raw, ctx, i) for i, raw in enumerate(raw_clips)]
        if any(c is None for c in clip_ids):
            continue
        n = len(clip_ids)

        explicit_duration = scene.get("duration")
        if explicit_duration is not None:
            per_clip = (float(explicit_duration) + (n - 1) * overlap) / n
            lengths = [per_clip] * n
        else:
            lengths = []
            probe_failed = False
            for clip_id in clip_ids:
                spec = bin_by_id[clip_id]
                kind = spec.type or probe.guess_type(spec.path)
                if kind == "image":
                    lengths.append(preset["image"])
                    continue
                try:
                    source_s = probe.probe_duration_seconds(str(spec.path))
                except probe.ProbeError as exc:
                    errors.append(f"{ctx}: {exc}")
                    probe_failed = True
                    continue
                lengths.append(min(source_s, preset["video_cap"]))
            if probe_failed:
                continue

        start = cursor
        for i, (clip_id, length) in enumerate(zip(clip_ids, lengths)):
            # With dissolve, only the "top" clip in each transition carries
            # the fade: it alternates, so it's exactly the odd indices.
            is_top = transition == "dissolve" and i % 2 == 1
            fade_in = overlap if (is_top and i > 0) else None
            fade_out = overlap if (is_top and i < n - 1) else None
            placement = ClipPlacementSpec(
                clip=clip_id,
                start=start,
                in_=0.0,
                duration=length,
                fade_in=fade_in,
                fade_out=fade_out,
            )
            track_clips[i % 2].append(placement)
            start += length - overlap

        cursor = start

    if errors:
        return doc

    tracks = list(doc.tracks)
    for name, clips in zip(SCENE_TRACK_NAMES, track_clips):
        if clips:
            tracks.append(TrackSpec(type="video", name=name, clips=clips))

    return ProjectDoc(
        project=doc.project,
        bin=new_bin,
        tracks=tracks,
        source_path=doc.source_path,
        raw_extras=doc.raw_extras,
        scenes_raw=[],
    )
