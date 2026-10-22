"""`parts:`: a project in several files. The main file has `project:`
and a list of parts, each a file of its own - its `bin:`, `scenes:`,
`timeline:`... - or a file that lists parts in turn:

    # trip.yaml
    project: {id: trip, title: Trip, fps: 25, width: 1920, height: 1080}
    presets_file: presets.yaml
    parts:
      - intro.yaml
      - beach.yaml
      - {file: mountains/all.yaml, transition: gl, gl: cube}

Each part becomes a sequence of its own - kdenlive's nested sequences
(its own tests/dataset/test-nesting.kdenlive: a sequence's tractor,
placed in another's track by its uuid) - and a file with `parts:` one
that plays them one after the other, alternating between two tracks so
that one can overlap the next by a transition: the keys a scene takes
for it (`transition`, `transition_duration`, `luma`... `gl`...) on the
incoming part's entry. Burst transitions (glitch, delaygrab,
cairogradient) aren't transitions between two pictures: not here.

A part file has no `project:` - it takes the main file's - and resolves
its own relative paths, against its own folder. Its bin ids are its
own: they're kept apart from other parts' (namespaced by the part's
path), so two files may both have a clip `a`. Presets: a file's are in
force in the parts it includes, which may add their own. A part that
includes itself, through any number of others, is an error.

`video-as-code render trip.yaml --part mountains/all` renders one part
alone (by the path of its file without `.yaml`, relative to the main
file's folder).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from . import gltransitions, lumas, meltpass
from . import presets as presets_mod
from .errors import ValidationErrors
from .i18n import msg
from .ids import deterministic_uuid
from .models import (
    AffineTransitionSpec,
    BinClipSpec,
    ClipPlacementSpec,
    MeltTransitionSpec,
    ProjectDoc,
    ResolvedProject,
    TrackSpec,
    WipeTransitionSpec,
)

PARTS_TRACKS = ("Parts A", "Parts B")  # (alternating, like scenes.py's)
_CONTAINER_KEYS = {"project", "parts"}  # (presets ones are gone once expanded)
_TRANSITION_KEYS = {
    "transition", "transition_duration", "luma", "luma_softness", "luma_reverse", "style", "gl", "gl_params",
    "transition_properties",
}
_BETWEEN_PARTS = ("none", "dissolve", "wipe", "affine", "iris", "flip", "gl")


def _read(path: Path) -> dict:
    if not path.is_file():
        raise ValidationErrors([msg("validation.yaml_missing", path=path)])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationErrors([msg("validation.yaml_invalid", path=path, exc=exc)]) from exc
    if not isinstance(data, dict):
        raise ValidationErrors([msg("validation.parts_file_not_map", path=path)])
    return data


def has_parts(path: str | Path) -> bool:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and "parts" in data


def resolve(path: str | Path, part: Optional[str] = None) -> ResolvedProject:
    """The project of a main file with `parts:` - or, with `part`, that
    one part alone."""
    main = Path(path).resolve()
    data = _read(main)
    if not isinstance(data.get("project"), dict):
        raise ValidationErrors([msg("validation.project_section_missing", path=main)])
    project_raw = data["project"]
    resolved = _resolve_file(main, data, project_raw, None, (main,), "", main.parent)
    _number(resolved, deterministic_project_id(project_raw))
    if part is None:
        return resolved
    found = _find(resolved, part.strip("/"))
    if found is None:
        raise ValidationErrors([msg("validation.part_unknown", part=part, known=", ".join(_part_names(resolved)) or "-")])
    return found


def deterministic_project_id(project_raw: dict) -> str:
    return str(project_raw.get("id", "project"))


def _resolve_file(
    file: Path, data: dict, project_raw: dict, inherited: Optional[dict], chain: tuple[Path, ...], prefix: str, root_dir: Path
) -> ResolvedProject:
    from . import scenes as scenes_mod  # (schema imports scenes lazily too)
    from . import schema

    errors: list[str] = []
    data, presets = presets_mod.expand_with(data, file, errors, inherited)
    if errors:
        raise ValidationErrors(errors)

    if "parts" not in data:  # a part with clips of its own
        if "project" in data and file != chain[0]:
            raise ValidationErrors([msg("validation.part_has_project", path=file)])
        doc = schema.load_yaml(file, data={**data, "project": project_raw})
        if doc.scenes_raw:
            doc = scenes_mod.compile_scenes(doc, doc.scenes_raw, errors)
            if errors:
                raise ValidationErrors([f"{file.name}: {e}" for e in errors])
        try:
            resolved = schema.validate_and_resolve(doc)
        except ValidationErrors as exc:
            raise ValidationErrors([f"{file.name}: {e}" for e in exc.errors]) from exc
        for clip in resolved.bin_clips:  # (each file's ids its own)
            clip.id = prefix + clip.id
        resolved.part_path = _part_name(file, root_dir)
        return resolved

    extra = set(data) - _CONTAINER_KEYS
    if extra:
        raise ValidationErrors([msg("validation.parts_with_clips", path=file, keys=", ".join(sorted(extra)))])
    if "project" in data and file != chain[0]:
        raise ValidationErrors([msg("validation.part_has_project", path=file)])
    entries = data["parts"]
    if not isinstance(entries, list) or not entries:
        raise ValidationErrors([msg("validation.parts_not_list", path=file)])

    children: list[tuple[ResolvedProject, dict, Path]] = []
    for i, entry in enumerate(entries):
        ctx = f"{file.name}: parts[{i}]"
        if isinstance(entry, str):
            entry = {"file": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or set(entry) - {"file"} - _TRANSITION_KEYS:
            raise ValidationErrors([msg("validation.part_entry_invalid", ctx=ctx, value=entry)])
        child = Path(entry["file"]) if Path(entry["file"]).is_absolute() else file.parent / entry["file"]
        child = child.resolve()
        if child in chain:
            raise ValidationErrors([msg("validation.parts_cycle", chain=" -> ".join(p.name for p in (*chain, child)))])
        child_prefix = f"{prefix}{_part_name(child, root_dir)}:"
        resolved = _resolve_file(child, _read(child), project_raw, presets, (*chain, child), child_prefix, root_dir)
        children.append((resolved, entry, child))
    container = _container(file, project_raw, children, prefix, errors)
    container.part_path = _part_name(file, root_dir) if file != chain[0] else ""
    return container


def _part_name(file: Path, root_dir: Path) -> str:
    """How --part names a part: its file's path from the main file's
    folder, without `.yaml`."""
    try:
        rel = file.resolve().relative_to(root_dir)
    except ValueError:
        rel = Path(file.name)
    return rel.with_suffix("").as_posix()


def _transition(entry: dict, ctx: str, a: str, b: str, start: float, errors: list[str]):
    """(overlap seconds, extra placement keys for the top clip, a spec or
    None) for the transition into a part, from its entry's keys."""
    kind = entry.get("transition", "none")
    is_melt = isinstance(kind, str) and kind.startswith(meltpass.PREFIX)
    if kind not in _BETWEEN_PARTS and not is_melt:
        errors.append(msg("validation.part_transition_invalid", ctx=ctx, transition=kind, valid=", ".join(_BETWEEN_PARTS)))
        return 0.0, None
    if kind == "none":
        return 0.0, None
    overlap = float(entry.get("transition_duration", 1.0))
    end = start + overlap
    if kind == "wipe":
        luma = entry.get("luma", "linear_x")
        problem = lumas.luma_problem(ctx, luma, "validation.scene_luma_invalid")
        softness = lumas.wipe_softness(entry.get("luma_softness", 0), ctx, "luma_softness", errors)
        if problem:
            errors.append(problem)
            return overlap, None
        return overlap, WipeTransitionSpec(a, b, start, end, luma, softness or 0.0, bool(entry.get("luma_reverse", False)))
    if kind == "affine":
        return overlap, AffineTransitionSpec(a, b, start, end, entry.get("style", "zoom"))
    if kind == "gl":
        service = gltransitions.parse(entry.get("gl"), entry.get("gl_params"), ctx, errors)
        return overlap, MeltTransitionSpec(a, b, start, end, service) if service else None
    if is_melt:
        service = meltpass.parse_service("transition", kind[len(meltpass.PREFIX):], entry.get("transition_properties"), ctx, errors)
        return overlap, MeltTransitionSpec(a, b, start, end, service) if service else None
    return overlap, kind  # dissolve, iris, flip: the top clip's fade (or reveal), as in scenes


def _container(
    file: Path, project_raw: dict, children: list[tuple[ResolvedProject, dict, Path]], prefix: str, errors: list[str]
) -> ResolvedProject:
    """A sequence that plays `children` one after the other, alternating
    between PARTS_TRACKS, each overlapping the one before by its entry's
    transition."""
    from . import schema

    project = schema._parse_project(project_raw, errors)
    fps = schema.resolve_fps(project.fps)
    tracks = [TrackSpec(type="video", name=name) for name in PARTS_TRACKS]
    bin_clips: list[BinClipSpec] = []
    wipes, affines, melts = [], [], []
    cursor = 0.0
    for i, (child, entry, child_file) in enumerate(children):
        clip_id = f"{prefix}{child.part_path}"
        child.title = child.part_path  # (its sequence's name in kdenlive)
        bin_clips.append(BinClipSpec(id=clip_id, path=child_file, type="sequence", sequence=child, folder=None))
        length = child.total_frames / fps.fps
        start, extra = cursor, {}
        if i > 0:
            ctx = f"{file.name}: parts[{i}]"
            overlap, spec = _transition(entry, ctx, PARTS_TRACKS[(i - 1) % 2], PARTS_TRACKS[i % 2], cursor, errors)
            if overlap:
                start = cursor - overlap
                if isinstance(spec, WipeTransitionSpec):
                    spec.start, spec.end = start, cursor
                    wipes.append(spec)
                elif isinstance(spec, AffineTransitionSpec):
                    spec.start, spec.end = start, cursor
                    affines.append(spec)
                elif isinstance(spec, MeltTransitionSpec):
                    spec.start, spec.end = start, cursor
                    melts.append(spec)
                elif spec in ("dissolve", "iris", "flip"):
                    # Parts B is above Parts A: the clip on it fades (or
                    # reveals) in when it comes in, out when it goes
                    if i % 2 == 1:
                        extra = {"fade_in": overlap, "reveal_style": None if spec == "dissolve" else spec}
                    else:
                        prev = tracks[(i - 1) % 2].clips[-1]
                        prev.fade_out = overlap
                        prev.reveal_style = None if spec == "dissolve" else spec
        tracks[i % 2].clips.append(
            ClipPlacementSpec(clip=clip_id, start=start, duration=length, **{k: v for k, v in extra.items() if v is not None or k != "reveal_style"})
        )
        cursor = start + length
    if errors:
        raise ValidationErrors(errors)
    doc = ProjectDoc(
        project=project, bin=bin_clips, tracks=[t for t in tracks if t.clips], source_path=file,
        wipe_transitions=wipes, affine_transitions=affines, melt_transitions=melts,
    )
    return schema.validate_and_resolve(doc)


def _number(root: ResolvedProject, project_id: str) -> None:
    """Every bin clip of every part a kdenlive id of its own, and every
    part's sequence a uuid - kdenlive needs them unique in the document."""
    seen: dict[int, object] = {}
    n = 0
    sequences = []

    def walk(project: ResolvedProject) -> None:
        nonlocal n
        for clip in project.bin_clips:
            if clip.sequence is not None:
                walk(clip.sequence)
                sequences.append(clip)
            elif id(clip) not in seen:
                n += 1
                clip.numeric_id = n
                seen[id(clip)] = clip

    walk(root)
    for clip in sequences:
        n += 1
        clip.numeric_id = n
        clip.sequence_uuid = deterministic_uuid(project_id, "sequence", clip.id)


def _find(project: ResolvedProject, name: str) -> Optional[ResolvedProject]:
    for clip in project.bin_clips:
        if clip.sequence is not None:
            if clip.sequence.part_path == name:
                return clip.sequence
            found = _find(clip.sequence, name)
            if found is not None:
                return found
    return None


def _part_names(project: ResolvedProject) -> list[str]:
    out = []
    for clip in project.bin_clips:
        if clip.sequence is not None:
            out.append(clip.sequence.part_path)
            out += _part_names(clip.sequence)
    return out
