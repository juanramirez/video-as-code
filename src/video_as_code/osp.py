"""`build --format openshot`: the timeline as an OpenShot project (.osp)
instead of a .kdenlive - to finish the video by hand in OpenShot.

What carries over: the files, each track as a layer (the last track on
top, as in the .kdenlive), each clip with its trim and place, fades (on
the picture as its opacity, on the sound as its volume), `gain`/`mute`,
dissolves (fades already) and wipes - as OpenShot's own transition, a
Mask over a grayscale picture, the same idea as MLT's luma - and guides
as markers. A derive: of libopenshot's effects alone (`openshot:`, with
no other operation) is its input with the effects on it, editable in
OpenShot, instead of the generated file. Everything else - what melt
does and OpenShot has no counterpart for - is left out, each thing
named in a warning.

Two steps: `plan` makes, in plain Python, what the project needs; the
.osp itself is written by openshot_render.py, run with the Python that
has libopenshot (as for derive:'s `openshot:`) - each file's and clip's
JSON is libopenshot's own, so it's what this version of OpenShot
expects.

Checked by loading the result into libopenshot's Timeline - what
OpenShot plays and exports with - and rendering frames
(tests/test_osp.py): a fade's opacity moves on the frames it should, a
wipe's darker parts go first, as with MLT's luma.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Optional

from . import lumas
from . import openshot_effects
from .errors import KdenliveAsCodeError
from .i18n import msg
from .mltdata import default_cache_root
from .models import ResolvedPlacement, ResolvedProject

LAYER_STEP = 1_000_000  # OpenShot's own: layer 1000000, 2000000...
LINEAR = 1  # libopenshot's interpolation: 0 bezier, 1 linear, 2 constant

# a placement's fields this carries over; any other one set is named in a warning
_CARRIED = {"bin_clip", "start_frame", "in_frame", "out_frame", "fade_in_frames", "fade_out_frames",
            "fade_in_level", "fade_out_level", "gain", "mute"}
# the YAML's name for a field, where it isn't the field's own
_YAML_NAME = {"glsl_filter": "glsl", "melt_filters": "melt", "freeze_at_frame": "freeze_at", "rect": "position/size",
              "position_keyframes": "position (keyframes)", "speed_ramp_source_offset_s": "speed_ramp",
              "color_grade_keyframes": "color_grade (keyframes)"}
_KINDS = {"video", "audio", "image"}

# how wide the blend at a Mask's edge is, as a fraction of the picture's
# levels, for some of its `contrast` values - measured, rendering
# OpenShot's linear wipe (0.5.0); MLT's luma `softness` is that fraction
_CONTRAST_BLEND = [(0.0, 0.875), (1.0, 0.83), (3.0, 0.74), (10.0, 0.43), (30.0, 0.0)]


class OspError(KdenliveAsCodeError):
    pass


def contrast_for(softness: float) -> float:
    """The Mask `contrast` closest to MLT's luma `softness`."""
    pts = _CONTRAST_BLEND
    if softness >= pts[0][1]:
        return pts[0][0]
    for (c0, b0), (c1, b1) in zip(pts, pts[1:]):
        if b1 <= softness <= b0:
            return round(c0 + (c1 - c0) * (b0 - softness) / (b0 - b1), 2)
    return pts[-1][0]


def _id(*parts: object) -> str:
    """Ten capitals and digits, like OpenShot's ids - but the same each build."""
    digest = int(hashlib.sha1("::".join(map(str, parts)).encode("utf-8")).hexdigest(), 16)
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    out = ""
    for _ in range(10):
        digest, i = divmod(digest, len(chars))
        out += chars[i]
    return out


def _keyframe(*points: tuple[int, float]) -> dict:
    return {"Points": [{"co": {"X": x, "Y": y}, "interpolation": LINEAR} for x, y in points]}


def _ramp(p: ResolvedPlacement, start: float, end: float, scale: float = 1.0) -> list[tuple[int, float]]:
    """A fade in and out as keyframes. X counts a clip's own frames from
    1 - its trim included, as libopenshot does."""
    points: list[tuple[int, float]] = []
    if p.fade_in_frames > 0:
        points += [(p.in_frame + 1, start * scale), (p.in_frame + p.fade_in_frames, scale)]
    if p.fade_out_frames > 0:
        points += [(p.out_frame - p.fade_out_frames + 2, scale), (p.out_frame + 1, end * scale)]
    if not points and scale != 1.0:
        points = [(1, scale)]
    return points


def _unsupported(p: ResolvedPlacement) -> list[str]:
    names = []
    for f in dataclasses.fields(p):
        if f.name in _CARRIED:
            continue
        default = f.default if f.default is not dataclasses.MISSING else (
            f.default_factory() if f.default_factory is not dataclasses.MISSING else None)
        if getattr(p, f.name) != default:
            name = _YAML_NAME.get(f.name, f.name)
            if name not in names:
                names.append(name)
    return names


def _inverted(picture: Path) -> Path:
    """A wipe's picture with its levels turned round (a reversed wipe: the
    light parts first), once, in the cache."""
    key = hashlib.sha1(str(picture.resolve()).encode("utf-8")).hexdigest()[:12]
    target = default_cache_root() / "lumas-inverted" / f"{picture.stem}-{key}.png"
    if target.exists() and target.stat().st_mtime >= picture.stat().st_mtime:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.stem + ".partial.png")
    try:
        result = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(picture), "-frames:v", "1",
                                 "-vf", "format=gray,negate", str(partial)], capture_output=True, text=True)
    except FileNotFoundError:
        raise OspError(msg("lumas.ffmpeg_unavailable", path=picture))
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise OspError(msg("osp.invert_failed", path=picture, error=result.stderr.strip()))
    partial.replace(target)
    return target


def _wipe_picture(luma: str) -> Path:
    """OpenShot's own file for one of its transitions; else the .pgm MLT reads."""
    if luma.startswith(lumas.OPENSHOT_PREFIX):
        own = lumas.openshot_transitions().get(luma[len(lumas.OPENSHOT_PREFIX):])
        if own is not None:
            return own
    return lumas.luma_path(luma)


def plan(project: ResolvedProject) -> tuple[dict, list[str]]:
    """What the .osp needs (openshot_render.py's `--project` input) and
    the warnings about what's left out."""
    fps = project.fps
    left_out: dict[str, list[str]] = {}  # what -> where

    def leave_out(what: str, where: str) -> None:
        left_out.setdefault(what, []).append(where)

    layers, clips, files = [], [], []
    layer_of: dict[str, int] = {}
    for t_idx, track in enumerate(project.tracks):
        number = (t_idx + 1) * LAYER_STEP
        layer_of[track.name] = number
        layers.append({"id": _id(project.project_id, "layer", t_idx), "label": track.name, "number": number,
                       "y": 0, "lock": track.locked})
        if track.hidden:
            leave_out("hidden", msg("osp.where_track", track=track.name))
        if track.pan is not None:
            leave_out("pan", msg("osp.where_track", track=track.name))
        audio = track.kind == "audio"
        for c_idx, p in enumerate(track.placements):
            clip = p.bin_clip
            where = msg("osp.where_clip", clip=clip.id, track=track.name, at=f"{p.start_frame / fps.fps:g}")
            if clip.kind not in _KINDS:
                leave_out(f"type: {clip.kind}" if clip.kind != "sequence" else "parts", where)
                continue
            for name in _unsupported(p):
                leave_out(name, where)
            path = clip.openshot_input or clip.path
            path = str(Path(path).resolve())
            if path not in files:
                files.append(path)
            props: dict = {}
            if audio:
                props["has_video"] = _keyframe((1, 0.0))
                silent = p.mute or track.audio_muted
                volume = [(1, 0.0)] if silent else _ramp(p, p.fade_in_level, p.fade_out_level, p.gain or 1.0)
                if volume:
                    props["volume"] = _keyframe(*volume)
            else:
                # a video track's clip plays no sound, as in the .kdenlive
                props["has_audio"] = _keyframe((1, 0.0))
                if p.gain is not None or p.mute:
                    leave_out("gain/mute", where)
                alpha = _ramp(p, 0.0, 0.0)
                if alpha:
                    props["alpha"] = _keyframe(*alpha)
            clips.append({
                "id": _id(project.project_id, "clip", t_idx, c_idx),
                "path": path,
                "title": clip.id,
                "layer": number,
                "position": p.start_frame / fps.fps,
                "start": p.in_frame / fps.fps,
                "end": (p.out_frame + 1) / fps.fps,
                "props": props,
                "effects": [json.loads(e) for e in clip.openshot_effects],
            })

    transitions = []
    order = {t.name: i for i, t in enumerate(project.tracks)}
    for w_idx, w in enumerate(project.wipe_transitions):
        frames = w.end_frame - w.start_frame + 1
        # a Mask uncovers the clip on its layer, darker parts first: on
        # the upper track, either coming in (track_b on top) or, in
        # reverse with the picture turned round, going away
        coming_in = order[w.track_b] > order[w.track_a]
        upper = w.track_b if coming_in else w.track_a
        picture = _wipe_picture(w.luma)
        if w.reverse != (not coming_in):
            picture = _inverted(picture)
        b0, b1 = (1.0, -1.0) if coming_in else (-1.0, 1.0)
        transitions.append({
            "id": _id(project.project_id, "wipe", w_idx),
            "layer": layer_of[upper],
            "position": w.start_frame / fps.fps,
            "end": frames / fps.fps,
            "picture": str(picture),
            "brightness": _keyframe((1, b0), (frames + 1, b1)),  # as OpenShot itself does
            "contrast": _keyframe((1, contrast_for(w.softness))),
        })
    for a in project.affine_transitions:
        leave_out(f"transition: {a.style}", msg("osp.where_at", at=f"{a.start_frame / fps.fps:g}"))
    for m in project.melt_transitions:
        leave_out(f"transition: {m.melt.service}", msg("osp.where_at", at=f"{m.start_frame / fps.fps:g}"))

    markers = [{"id": _id(project.project_id, "marker", i), "position": g.frame / fps.fps,
                "icon": "blue.png", "vector": "blue", "label": g.text} for i, g in enumerate(project.guides)]
    out = {
        "id": "T0",
        "fps": {"num": fps.num, "den": fps.den},
        "width": project.width,
        "height": project.height,
        "display_ratio": _ratio(project.width, project.height),
        "pixel_ratio": {"num": 1, "den": 1},
        "sample_rate": 48000,
        "channels": 2,
        "channel_layout": 3,
        "duration": max(project.total_frames / fps.fps, 1.0),
        "layers": layers,
        "files": files,
        "clips": clips,
        "transitions": transitions,
        "markers": markers,
    }
    warnings = [msg("osp.left_out", what=what, where="; ".join(wheres[:3]) + (msg("osp.and_more", n=len(wheres) - 3) if len(wheres) > 3 else ""))
                for what, wheres in left_out.items()]
    return out, warnings


def _ratio(w: int, h: int) -> dict:
    g = math.gcd(w, h)
    return {"num": w // g, "den": h // g}


def write(project: ResolvedProject, output: Path) -> list[str]:
    """Writes the .osp; returns the warnings about what's left out."""
    exe = openshot_effects.python()
    if exe is None:
        raise OspError(msg("osp.needs_openshot", env=openshot_effects.PYTHON_ENV))
    data, warnings = plan(project)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    plan_file = output.with_name(output.name + ".plan.json")
    plan_file.write_text(json.dumps(data), encoding="utf-8")
    try:
        result = subprocess.run([exe, "-I", str(openshot_effects._SCRIPT), "--project", str(plan_file), str(output)],
                                capture_output=True, text=True, timeout=600)
    finally:
        plan_file.unlink(missing_ok=True)
    if result.returncode != 0:
        tail = "\n".join(line for line in result.stderr.splitlines() if not line.startswith("["))[-2000:]
        raise OspError(msg("osp.write_failed", path=output, error=tail.strip()))
    return warnings
