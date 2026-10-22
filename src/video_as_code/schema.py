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

import os

import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from . import autoprune
from . import derive as derive_mod
from . import probe, sources_lock, titles
from . import upscale as upscale_mod
from .errors import ValidationErrors
from .i18n import msg
from . import gltransitions, lumas, meltpass
from . import presets as presets_mod
from .models import (
    AFFINE_STYLES,
    LUT_INTERPS,
    LUT_SUFFIXES,
    AffineTransitionSpec,
    MeltTransitionSpec,
    BinClipSpec,
    ClipPlacementSpec,
    DeriveSpec,
    GuideSpec,
    ProjectDoc,
    ProjectSpec,
    ResolvedAffineTransition,
    ResolvedMeltTransition,
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


# A part of a project in several files has no `project:` of its own: how to
# read each one registered (parts.register) - the file again, with the
# presets in force there and the main file's `project:` - so that anything
# that loads a part file (fetch, its interactive fixes, autoprune) can.
PART_LOADERS: dict[Path, Callable[[], dict]] = {}


def load_yaml(path: str | Path, data: Optional[dict] = None) -> ProjectDoc:
    """`data`: the file's contents already read (a part of a project, with
    the main file's `project:` put in - parts.py); else it's read here."""
    path = Path(path).resolve()
    if data is None and path in PART_LOADERS:
        data = PART_LOADERS[path]()
    if data is None:
        if not path.exists():
            raise ValidationErrors([msg("validation.yaml_missing", path=path)])
        with path.open("r", encoding="utf-8") as fh:
            try:
                data = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                raise ValidationErrors([msg("validation.yaml_invalid", path=path, exc=exc)]) from exc

    errors: list[str] = []
    source_dir = path.parent

    # `use:` -> its presets' keys, before anything reads them (presets.py)
    if isinstance(data, dict):
        data = presets_mod.expand(data, path, errors)
        if errors:
            raise ValidationErrors(errors)

    project_data = data.get("project")
    if not isinstance(project_data, dict):
        raise ValidationErrors([msg("validation.project_section_missing", path=path)])

    project = _parse_project(project_data, errors)

    bin_data = data.get("bin", [])
    if not isinstance(bin_data, list):
        errors.append(msg("validation.bin_not_list"))
        bin_data = []
    lock = sources_lock.read(source_dir)
    links_lock = sources_lock.read_links(source_dir)
    bin_clips = [
        c for c in (_parse_bin_clip(item, source_dir, project, lock, links_lock, errors) for item in bin_data) if c
    ]
    bin_clips = _resolve_derive_paths(bin_clips, source_dir / project.sources_dir / derive_mod.DERIVED_DIR, errors)

    timeline_data = data.get("timeline", {})
    tracks_data = timeline_data.get("tracks", []) if isinstance(timeline_data, dict) else []
    if not isinstance(tracks_data, list):
        errors.append(msg("validation.tracks_not_list"))
        tracks_data = []
    fps = resolve_fps(project.fps)
    tracks = [t for t in (_parse_track(item, idx, errors, fps) for idx, item in enumerate(tracks_data)) if t]

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
    melt_transitions: list[MeltTransitionSpec] = []
    for idx, item in enumerate(transitions_data):
        parsed = _parse_transition(item, idx, track_names, errors)
        if isinstance(parsed, WipeTransitionSpec):
            wipe_transitions.append(parsed)
        elif isinstance(parsed, AffineTransitionSpec):
            affine_transitions.append(parsed)
        elif isinstance(parsed, MeltTransitionSpec):
            melt_transitions.append(parsed)

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
        melt_transitions=melt_transitions,
    )


_MLT_DUST_PICTURES = ("dust1", "dust2", "dust3", "dust4", "dust5")


def _dust_pictures(pictures: tuple[str, ...] | None, base: Path, ctx: str, errors: list[str]) -> tuple[str, ...] | None:
    """`film_dust: {pictures}` resolved: MLT's own by name, the rest as
    absolute paths (relative to the project file) - existing .svg files."""
    if pictures is None:
        return None
    out = []
    for p in pictures:
        if p in _MLT_DUST_PICTURES:
            out.append(p)
            continue
        path = Path(p) if Path(p).is_absolute() else base / p
        if path.suffix.lower() != ".svg" or not path.is_file():
            errors.append(msg("validation.film_dust_picture_missing", ctx=ctx, picture=p))
        out.append(str(path.resolve()))
    return tuple(out)


FONT_WEIGHTS = {"thin": 100, "light": 300, "normal": 400, "medium": 500, "semibold": 600, "bold": 700, "black": 900}


def _rgba(value: Any) -> Optional[tuple[int, int, int, int]]:
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(
        isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in value
    ):
        return tuple(value)
    return None


def _title_style(item: dict, errors: list[str]) -> Optional[dict]:
    """A title's `font_weight`, `italic`, `outline`/`outline_color` and
    `shadow` (see titles.py) - None if one is wrong (and why, in errors)."""
    clip_id = item.get("id")
    before = len(errors)
    weight = item.get("font_weight", 400)
    if isinstance(weight, str):
        weight = FONT_WEIGHTS.get(weight.lower(), weight)
    if isinstance(weight, bool) or not isinstance(weight, int) or not 100 <= weight <= 900:
        errors.append(msg("validation.title_font_weight_invalid", id=clip_id, value=item.get("font_weight"),
                          names=", ".join(FONT_WEIGHTS)))
    italic = item.get("italic", False)
    if not isinstance(italic, bool):
        errors.append(msg("validation.title_italic_invalid", id=clip_id, value=italic))
    outline = item.get("outline", 0)
    if isinstance(outline, bool) or not isinstance(outline, (int, float)) or outline < 0:
        errors.append(msg("validation.title_outline_invalid", id=clip_id, value=outline))
    outline_color = _rgba(item.get("outline_color", [0, 0, 0, 255]))
    if outline_color is None:
        errors.append(msg("validation.title_color_invalid", id=clip_id, field="outline_color", value=item.get("outline_color")))
    shadow_raw = item.get("shadow", False)
    shadow = None
    if shadow_raw is True:
        shadow = ((0, 0, 0, 160), 6, 3, 3)
    elif isinstance(shadow_raw, dict) and not set(shadow_raw) - {"color", "blur", "offset"}:
        color = _rgba(shadow_raw.get("color", [0, 0, 0, 160]))
        blur = shadow_raw.get("blur", 6)
        offset = shadow_raw.get("offset", [3, 3])
        if (color is not None and isinstance(blur, int) and not isinstance(blur, bool) and blur >= 0
                and isinstance(offset, (list, tuple)) and len(offset) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in offset)):
            shadow = (color, blur, int(offset[0]), int(offset[1]))
        else:
            errors.append(msg("validation.title_shadow_invalid", id=clip_id, value=shadow_raw))
    elif shadow_raw is not False and shadow_raw is not None:
        errors.append(msg("validation.title_shadow_invalid", id=clip_id, value=shadow_raw))
    if len(errors) > before:
        return None
    return {"font_weight": weight, "italic": italic, "outline": float(outline), "outline_color": outline_color, "shadow": shadow}


def lut_dirs(project_dir: Path) -> list[Path]:
    """Where `lut:` looks a LUT up by name: `luts/` next to the project
    file, then the user's own (<XDG data dir>/video-as-code/luts)."""
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return [project_dir / "luts", Path(data) / "video-as-code" / "luts"]


def luts_by_name(project_dir: Path) -> dict[str, Path]:
    """name -> file, of the LUTs `lut:` finds by name (the first folder's win)."""
    found: dict[str, Path] = {}
    for folder in reversed(lut_dirs(project_dir)):
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() in LUT_SUFFIXES:
                    found[path.stem] = path
    return found


def _parse_lut(raw: Any, ctx: str, errors: list[str]) -> Optional[str]:
    """`lut:` as written: a file name, or a LUT's name (resolved by _lut_file)."""
    if raw is None:
        return None
    named = isinstance(raw, str) and raw and not Path(raw).suffix and "/" not in raw
    if not named and (not isinstance(raw, str) or Path(raw).suffix.lower() not in LUT_SUFFIXES):
        errors.append(msg("validation.lut_invalid", ctx=ctx, value=raw, suffixes=", ".join(LUT_SUFFIXES)))
        return None
    return raw


def _parse_lut_interp(raw: Any, ctx: str, errors: list[str]) -> str:
    if raw not in LUT_INTERPS:
        errors.append(msg("validation.lut_interp_invalid", ctx=ctx, value=raw, valid=", ".join(LUT_INTERPS)))
        return "tetrahedral"
    return raw


def _lut_file(lut: str | None, base: Path, ctx: str, errors: list[str]) -> str | None:
    """`lut:` as an absolute path (relative to the project file) - an existing file."""
    if lut is None:
        return None
    if not Path(lut).suffix and "/" not in lut:  # a name: in lut_dirs
        known = luts_by_name(base)
        if lut not in known:
            errors.append(msg("validation.lut_unknown", ctx=ctx, name=lut, known=", ".join(sorted(known)) or "-",
                              dirs=", ".join(str(d) for d in lut_dirs(base))))
            return None
        return str(known[lut].resolve())
    path = Path(lut) if Path(lut).is_absolute() else base / lut
    if not path.is_file():
        errors.append(msg("validation.lut_missing", ctx=ctx, path=path))
    return str(path.resolve())


def load_and_resolve(path: str | Path, part: Optional[str] = None) -> ResolvedProject:
    """High-level entry point: loads the YAML, compiles `scenes:` (if any)
    into explicit tracks/clips, and validates+resolves the result. A
    project in several files (`parts:`) goes through parts.py - which
    `part`, the path of one of them, picks out alone."""
    from . import parts as parts_mod  # (it uses this module)
    from . import scenes as scenes_mod  # deferred import: avoids a cycle with schema

    if parts_mod.has_parts(path):
        return parts_mod.resolve(path, part)
    if part is not None:
        raise ValidationErrors([msg("validation.part_without_parts", path=path, part=part)])
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
            errors.append(msg("validation.project_field_required", field=key))
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
    sources = data.get("sources") or {}
    if not isinstance(sources, dict):
        errors.append(msg("validation.sources_invalid", sources=sources))
        sources = {}
    album_url, album_name = _parse_album(sources.get("album"), errors)
    reference_urls, reference_path = _parse_reference(sources.get("reference"), errors)
    prune_days = sources.get("prune_after_days", autoprune.DEFAULT_DAYS)
    if isinstance(prune_days, bool) or not isinstance(prune_days, (int, float)) or prune_days < 0:
        errors.append(msg("validation.prune_after_days_invalid", value=prune_days))
        prune_days = autoprune.DEFAULT_DAYS
    return ProjectSpec(
        id=str(data["id"]),
        title=str(data["title"]),
        fps=float(data["fps"]),
        width=int(data["width"]),
        height=int(data["height"]),
        colorspace=colorspace,
        default_image_duration=float(data.get("default_image_duration", 4.0)),
        notes=str(data["notes"]) if data.get("notes") is not None else None,
        album_url=album_url,
        album_name=album_name,
        reference_urls=reference_urls,
        reference_path=reference_path,
        sources_dir=str(sources.get("dir", "assets")),
        prune_after_days=float(prune_days),
    )


def _parse_album(raw, errors: list[str]) -> tuple[Optional[str], Optional[str]]:
    """`project.sources.album` -> (url, name): a shared-album link, or a
    map with the album's `name` (to create it under, see albums.py) and/or
    its `url` (its link, once it exists)."""
    if not raw:
        return None, None
    if not isinstance(raw, dict):
        return str(raw), None
    name, url = raw.get("name"), raw.get("url")
    if (set(raw) - {"name", "url"} or not (name or url)
            or any(v is not None and not isinstance(v, str) for v in (name, url))):
        errors.append(msg("validation.album_invalid", album=raw))
        return None, None
    return url or None, name or None


def _parse_source(raw, clip_id, errors: list[str]) -> Optional[str]:
    """A `source:` (a bin clip's, or a derive: input's) as the key it's
    known by (sources_lock.source_key): an album item's original file name,
    or `{name, taken, size, id}` - that name plus its capture time, its
    size in pixels and/or its album item id (the lock's `media_id`), for
    when several items of the album share the name and the name alone
    can't say which one is meant. None (with the error) if it's neither."""
    size = media_id = None
    if isinstance(raw, dict):
        name, taken = raw.get("name"), sources_lock.normalize_taken(raw.get("taken"))
        size = _number_pair(raw.get("size")) if raw.get("size") is not None else None
        size = (int(size[0]), int(size[1])) if size and all(v > 0 and v == int(v) for v in size) else None
        media_id = raw.get("id")
        if (set(raw) - {"name", "taken", "size", "id"} or not isinstance(name, str)
                or (raw.get("taken") is not None and taken is None)
                or (raw.get("size") is not None and size is None)
                or (media_id is not None and not (isinstance(media_id, str) and sources_lock.MEDIA_ID_RE.match(media_id)))
                or (taken is None and size is None and media_id is None)):
            errors.append(msg("validation.source_map_invalid", id=clip_id, source=raw))
            return None
    else:
        name, taken = str(raw), None
    if Path(name).name != name or name in ("", ".", "..") or sources_lock.source_key(name) != name or sources_lock._parts(name)[0] != name:
        errors.append(msg("validation.bin_source_not_a_name", id=clip_id, source=name))
        return None
    return sources_lock.source_key(name, taken, size, media_id)


def _parse_reference(raw, errors: list[str]) -> tuple[Optional[tuple[str, ...]], str]:
    """`project.sources.reference` -> (links, path): a link, a list of
    links, or a map with `url` (one or a list) and `path` (where to keep
    it, default reference/reference.mp4)."""
    default = ProjectSpec.__dataclass_fields__["reference_path"].default
    if raw is None:
        return None, default
    urls, path = (raw.get("url"), raw.get("path", default)) if isinstance(raw, dict) else (raw, default)
    urls = [urls] if isinstance(urls, str) else urls
    if (isinstance(raw, dict) and set(raw) - {"url", "path"}
            or not isinstance(urls, list) or not urls
            or not all(isinstance(u, str) and u.strip() for u in urls)
            or not isinstance(path, str) or not Path(path).suffix):
        errors.append(msg("validation.reference_invalid", reference=raw))
        return None, default
    return tuple(urls), path


def _parse_bin_clip(
    item: Any,
    source_dir: Path,
    project: ProjectSpec,
    lock: dict[str, dict],
    links_lock: dict[str, dict],
    errors: list[str],
) -> BinClipSpec | None:
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
        style = _title_style(item, errors)
        if style is None:
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
            **style,
        )

    if item.get("derive") is not None:
        if "path" in item or item.get("source") is not None:
            errors.append(msg("validation.derive_and_path", id=item.get("id")))
            return None
        spec = _parse_derive(item["derive"], str(item.get("id")), source_dir, project, errors)
        if spec is None:
            return None
        # `path` is filled in by _resolve_derive_paths once every bin clip
        # is parsed (a `clip:` input may be declared further down).
        return BinClipSpec(id=str(item["id"]), path=None, type=clip_type, folder=folder, derive=spec)

    if item.get("url") is not None:
        # See BinClipSpec.url: downloaded by `fetch` from the first link
        # that works, stored as `<id><ext>` (sources_lock.link_local_name).
        urls = item["url"] if isinstance(item["url"], list) else [item["url"]]
        if "path" in item or item.get("source") is not None:
            errors.append(msg("validation.bin_url_and_path", id=item.get("id")))
            return None
        if not urls or not all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in urls):
            errors.append(msg("validation.bin_url_invalid", id=item.get("id"), url=item["url"]))
            return None
        override = str(item["local_name"]) if item.get("local_name") is not None else None
        if override is not None and (Path(override).name != override or override in ("", ".", "..")):
            errors.append(msg("validation.local_name_not_a_name", id=item.get("id"), name=override))
            return None
        local = sources_lock.link_local_name(str(item["id"]), links_lock.get(str(item["id"])), override)
        return BinClipSpec(
            id=str(item["id"]),
            path=source_dir / project.sources_dir / local,
            type=clip_type,
            folder=folder,
            url=[str(u) for u in urls],
            local_name=override,
        )

    if item.get("source") is not None:
        # See BinClipSpec.source: an item of the project's shared album,
        # downloaded by `fetch` under its original file name.
        source = _parse_source(item["source"], item.get("id"), errors)
        if source is None:
            return None
        if "path" in item:
            errors.append(msg("validation.bin_source_and_path", id=item.get("id")))
            return None
        if not (project.album_url or project.album_name):
            errors.append(msg("validation.bin_source_without_album", id=item.get("id")))
            return None
        override = str(item["local_name"]) if item.get("local_name") is not None else None
        if override is not None:
            if Path(override).name != override or override in ("", ".", ".."):
                errors.append(msg("validation.local_name_not_a_name", id=item.get("id"), name=override))
                return None
            # the extension decides how the file is probed and played
            if Path(override).suffix.lower() != Path(sources_lock.split_source(source)[0]).suffix.lower():
                errors.append(msg("validation.local_name_extension", id=item.get("id"), name=override, source=source))
                return None
        local = sources_lock.local_name(str(item["id"]), source, lock.get(source), override)
        return BinClipSpec(
            id=str(item["id"]),
            path=source_dir / project.sources_dir / local,
            type=clip_type,
            folder=folder,
            source=source,
            local_name=override,
        )

    if item.get("local_name") is not None:
        errors.append(msg("validation.local_name_without_source", id=item.get("id")))
        return None
    if "path" not in item:
        errors.append(msg("validation.bin_entry_invalid", item=item))
        return None
    raw_path = Path(item["path"])
    resolved = raw_path if raw_path.is_absolute() else (source_dir / raw_path)
    return BinClipSpec(id=str(item["id"]), path=resolved, type=clip_type, folder=folder)


def _number_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return None
    return float(value[0]), float(value[1])


def _parse_derive(
    data: Any, clip_id: str, source_dir: Path, project: ProjectSpec, errors: list[str]
) -> DeriveSpec | None:
    """A bin clip's `derive:` map (see models.DeriveSpec). Only checks the
    spec itself; whether its input exists is validate_and_resolve's job."""
    if not isinstance(data, dict):
        errors.append(msg("validation.derive_not_a_map", id=clip_id, value=data))
        return None
    n_errors = len(errors)
    known = {"source", "path", "clip", "motion", "rotate", "trim", "cut", "crop", "scale", "devignette", "fit", "feather", "vignette", "fps", "reverse", "volume", "upscale", "intra"}
    unknown = set(data) - known
    if unknown:
        errors.append(msg("validation.derive_unknown_keys", id=clip_id, keys=", ".join(sorted(unknown))))

    inputs = [k for k in ("source", "path", "clip") if data.get(k) is not None]
    if len(inputs) != 1:
        errors.append(msg("validation.derive_one_input", id=clip_id))
        return None
    source = path = clip = None
    if inputs[0] == "source":
        source = _parse_source(data["source"], clip_id, errors)
        if source is not None and not (project.album_url or project.album_name):
            errors.append(msg("validation.bin_source_without_album", id=clip_id))
    elif inputs[0] == "path":
        raw_path = Path(str(data["path"]))
        path = raw_path if raw_path.is_absolute() else source_dir / raw_path
    else:
        clip = str(data["clip"])

    trim = None
    if data.get("trim") is not None:
        trim = _number_pair(data["trim"])
        if trim is None or trim[0] < 0 or trim[1] <= trim[0]:
            errors.append(msg("validation.derive_trim_invalid", id=clip_id, value=data["trim"]))
    cut = None
    if data.get("cut") is not None:
        # [[start, end], ...]: ranges to remove, in the input's seconds,
        # in order and not overlapping
        pairs = [_number_pair(r) for r in data["cut"]] if isinstance(data["cut"], list) else [None]
        ok = bool(pairs) and all(p is not None and 0 <= p[0] < p[1] for p in pairs)
        ok = ok and all(a[1] <= b[0] for a, b in zip(pairs, pairs[1:]))
        if ok:
            cut = tuple(pairs)
        else:
            errors.append(msg("validation.derive_cut_invalid", id=clip_id, value=data["cut"]))
    crop = None
    if data.get("crop") is not None:
        raw = data["crop"]
        try:
            crop = tuple(int(raw[k]) for k in ("x", "y", "w", "h"))
        except (TypeError, KeyError, ValueError):
            crop = None
        if crop is None or crop[0] < 0 or crop[1] < 0 or crop[2] <= 0 or crop[3] <= 0:
            errors.append(msg("validation.derive_crop_invalid", id=clip_id, value=raw))
    scale = None
    if data.get("scale") is not None:
        pair = _number_pair(data["scale"])
        scale = (int(pair[0]), int(pair[1])) if pair else None
        # -1 on one side keeps the aspect ratio (ffmpeg's own convention)
        if scale is None or any(v == 0 or v < -1 for v in scale) or scale == (-1, -1):
            errors.append(msg("validation.derive_scale_invalid", id=clip_id, value=data["scale"]))
    rotate = data.get("rotate")
    if rotate is not None and (isinstance(rotate, bool) or rotate not in (90, 180, 270)):
        errors.append(msg("validation.derive_rotate_invalid", id=clip_id, value=rotate))
    devignette = data.get("devignette")
    # the corners' brightness relative to the centre: 1 would be no
    # vignette at all, 0 black corners (nothing left to brighten)
    if devignette is not None and (
        isinstance(devignette, bool) or not isinstance(devignette, (int, float)) or not 0 < devignette < 1
    ):
        errors.append(msg("validation.derive_devignette_invalid", id=clip_id, value=devignette))
    fit = None
    fit_lighten = None
    fit_card: dict = {}
    if data.get("fit") is not None:
        # {size: [w, h], background: blur | black, lighten}: the whole picture
        # inside w x h, what's left filled with a blurred copy of itself
        # (brightened by `lighten`, 0-1, its colours kept) or black
        raw = data["fit"]
        size = _number_pair(raw.get("size")) if isinstance(raw, dict) else None
        background = raw.get("background", "blur") if isinstance(raw, dict) else None
        lighten = raw.get("lighten") if isinstance(raw, dict) else None
        card = {k: raw.get(k) for k in ("margin", "radius", "edge", "shadow", "dim")} if isinstance(raw, dict) else {}
        ok = isinstance(raw, dict) and not set(raw) - {"size", "background", "lighten", "margin", "radius", "edge", "shadow", "dim"}
        # the card options: whole pixels (margin, radius, edge) or 0-1 (shadow, dim), blur only; not dim and lighten together
        is_num = lambda v: not isinstance(v, bool) and isinstance(v, (int, float))
        ok = ok and all(v is None or (background == "blur" and is_num(v)) for v in card.values())
        ok = ok and all(card[k] is None or (card[k] == int(card[k]) and card[k] >= 0) for k in ("margin", "radius", "edge"))
        ok = ok and all(card[k] is None or 0 <= card[k] < 1 for k in ("shadow", "dim"))
        ok = ok and not (card["dim"] and lighten)
        ok = ok and (not card["margin"] or (size is not None and 2 * card["margin"] < min(size)))
        ok = ok and size is not None and all(v > 0 and v == int(v) for v in size) and background in derive_mod.FIT_BACKGROUNDS
        ok = ok and (lighten is None or (background == "blur" and not isinstance(lighten, bool)
                                         and isinstance(lighten, (int, float)) and 0 <= lighten < 1))
        if ok:
            fit = (int(size[0]), int(size[1]), background)
            fit_lighten = float(lighten) if lighten else None
            fit_card = {k: (int(v) if k in ("margin", "radius", "edge") else float(v)) for k, v in card.items() if v}
        else:
            errors.append(msg("validation.derive_fit_invalid", id=clip_id, value=raw))
    feather = data.get("feather")
    if feather is not None and (isinstance(feather, bool) or not isinstance(feather, int) or feather <= 0):
        errors.append(msg("validation.derive_feather_invalid", id=clip_id, value=feather))
    vignette = data.get("vignette")
    # same scale as devignette: the corners' brightness relative to the centre
    if vignette is not None and (
        isinstance(vignette, bool) or not isinstance(vignette, (int, float)) or not 0 < vignette < 1
    ):
        errors.append(msg("validation.derive_vignette_invalid", id=clip_id, value=vignette))
    fps = data.get("fps")
    if fps is not None and (isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0):
        errors.append(msg("validation.derive_fps_invalid", id=clip_id, value=fps))
    reverse = data.get("reverse", False)
    if not isinstance(reverse, bool):
        errors.append(msg("validation.derive_reverse_invalid", id=clip_id, value=reverse))
    volume = data.get("volume")
    if volume is not None and (isinstance(volume, bool) or not isinstance(volume, (int, float)) or volume < 0):
        errors.append(msg("validation.derive_volume_invalid", id=clip_id, value=volume))
    motion = data.get("motion", False)
    if not isinstance(motion, bool):
        errors.append(msg("validation.derive_motion_invalid", id=clip_id, value=motion))
    intra = data.get("intra", False)
    if not isinstance(intra, bool):
        errors.append(msg("validation.derive_intra_invalid", id=clip_id, value=intra))
    upscale = data.get("upscale")
    # x4 only: it's what the realesrgan-x4plus model does (see upscale.py)
    if upscale is not None and (isinstance(upscale, bool) or upscale != upscale_mod.FACTOR):
        errors.append(msg("validation.derive_upscale_invalid", id=clip_id, value=upscale, factor=upscale_mod.FACTOR))
    if len(errors) > n_errors:
        return None
    if not (
        motion or rotate or trim or cut or crop or scale or devignette is not None or fit or feather or vignette is not None or fps
        or reverse or volume is not None or upscale or intra
    ):
        errors.append(msg("validation.derive_does_nothing", id=clip_id))
        return None
    return DeriveSpec(
        source=source,
        path=path,
        clip=clip,
        trim=trim,
        cut=cut,
        crop=crop,
        scale=scale,
        devignette=float(devignette) if devignette is not None else None,
        rotate=int(rotate) if rotate is not None else None,
        fit=fit,
        fit_lighten=fit_lighten,
        fit_margin=fit_card.get("margin"),
        fit_radius=fit_card.get("radius"),
        fit_edge=fit_card.get("edge"),
        fit_shadow=fit_card.get("shadow"),
        fit_dim=fit_card.get("dim"),
        feather=feather,
        vignette=float(vignette) if vignette is not None else None,
        fps=float(fps) if fps is not None else None,
        reverse=reverse,
        volume=float(volume) if volume is not None else None,
        upscale=int(upscale) if upscale is not None else None,
        motion=motion,
        intra=intra,
    )


def derive_input_path(clip: BinClipSpec, sources_root: Path, by_id: dict[str, BinClipSpec]) -> Path | None:
    """Where a derived clip's input file lives (None for an unknown or
    unresolved `clip:` reference)."""
    spec = clip.derive
    if spec.source is not None:
        return derive_mod.album_input_path(spec.source, list(by_id.values()), sources_root)
    if spec.path is not None:
        return spec.path
    ref = by_id.get(spec.clip)
    return ref.path if ref is not None else None


def _resolve_derive_paths(bin_clips: list[BinClipSpec], derived_dir: Path, errors: list[str]) -> list[BinClipSpec]:
    """Fills in `path` (the generated file, see derive.output_path) for
    every derived bin clip. Separate from parsing because a `clip:` input
    can be any bin clip, declared before or after, and may itself be
    derived - so they're resolved depth-first, rejecting cycles."""
    by_id = {c.id: c for c in bin_clips}
    sources_root = derived_dir.parent
    resolving: set[str] = set()

    def resolve(clip_id: str) -> BinClipSpec | None:
        clip = by_id[clip_id]
        if clip.derive is None or clip.path is not None:
            return clip
        if clip_id in resolving:
            errors.append(msg("validation.derive_cycle", id=clip_id))
            return None
        resolving.add(clip_id)
        try:
            spec = clip.derive
            if spec.clip is not None:
                if spec.clip not in by_id:
                    errors.append(msg("validation.derive_unknown_clip", id=clip_id, clip=spec.clip))
                    return None
                ref = resolve(spec.clip)
                if ref is None:
                    return None
                if ref.type == "title" or ref.path is None:
                    errors.append(msg("validation.derive_from_title", id=clip_id, clip=spec.clip))
                    return None
                input_path, input_ref = ref.path, f"clip:{ref.path.name}"
            elif spec.source is not None:
                # (input_ref names the album item, not where it's stored: same output either way)
                input_path = derive_mod.album_input_path(spec.source, bin_clips, sources_root)
                input_ref = f"source:{spec.source}"
            else:
                input_path, input_ref = spec.path, f"path:{spec.path.name}"
            resolved = replace(clip, path=derive_mod.output_path(clip_id, spec, input_path, input_ref, derived_dir))
            by_id[clip_id] = resolved
            return resolved
        finally:
            resolving.discard(clip_id)

    result = []
    for clip in bin_clips:
        if clip.derive is None:
            result.append(clip)
            continue
        # (with a duplicate id this resolves the last one declared; the
        # duplicate itself is reported by validate_and_resolve)
        resolved = resolve(clip.id)
        if resolved is not None:
            result.append(resolved)
    return result


def _boomerang_source_duration_s(placement: ClipPlacementSpec) -> float | None:
    """This clip's own duration in output-timeline seconds, computed only
    from what's on the placement itself (no source-file probing) - the
    same explicit-only subset `boomerang` requires. Mirrors the no-ramp
    branch of validate_and_resolve's own in/out resolution, minus the
    "fall back to the whole probed asset" case that needs the bin."""
    if placement.out is not None:
        return placement.out - placement.in_
    if placement.duration is not None:
        return placement.duration
    if placement.source_in is not None and placement.source_out is not None:
        speed = abs(placement.speed) if placement.speed else 1.0
        return abs(placement.source_out - placement.source_in) / speed
    return None


def _boomerang_source_window_s(placement: ClipPlacementSpec) -> tuple[float, float] | None:
    """Real (unwarped) source seconds [in, out) this boomerang placement
    covers - the exact window to hand `probe.render_reversed_segment` when
    pre-rendering its reverse half's derivative file. Same priority order
    as `_boomerang_source_duration_s`, but resolved back to real source
    time even for the `out:`/`duration:` forms (which are on the already-
    warped timeline when `speed:` is set - unlike `source_in:`/
    `source_out:`, which are always real seconds already)."""
    if placement.source_in is not None and placement.source_out is not None:
        return (placement.source_in, placement.source_out)
    speed = abs(placement.speed) if placement.speed else 1.0
    if placement.out is not None:
        return (placement.in_ * speed, placement.out * speed)
    if placement.duration is not None:
        return (placement.in_ * speed, (placement.in_ + placement.duration) * speed)
    return None


def _expand_boomerangs(
    clips: list[ClipPlacementSpec], track_idx: int, errors: list[str], fps: FrameRate
) -> list[ClipPlacementSpec]:
    """Expands each `boomerang: true` placement into itself (boomerang
    stripped) plus an auto-generated reverse repeat right after it - see
    ClipPlacementSpec.boomerang for the exact rules.

    The reverse's `start` is computed in *frames*, not raw seconds: the
    forward placement's own real on-timeline length always gets rounded
    to a whole frame later (validate_and_resolve's own
    `fps.seconds_to_frames`), so deriving the reverse's start from the
    unrounded float duration can land it a frame short of where the
    forward one actually ends, overlapping by one frame - reproduced and
    fixed while wiring this up against real-world clips (e.g.
    source_out=1.2s @ speed=1.935: 1.2/1.935 = 0.620155...s, which rounds to 19
    frames = 0.6333s, not the raw 0.6201s).

    The reverse placement keeps `source_in`/`source_out`/`speed` exactly
    as written (same segment, same *positive* speed as the forward half -
    no negation) and is instead marked via `_boomerang_reverse_of` so
    validate_and_resolve substitutes a pre-reversed derivative file for
    its bin clip - see ClipPlacementSpec.boomerang's docstring for why
    negative `speed:` isn't used here."""
    expanded: list[ClipPlacementSpec] = []
    for c_idx, placement in enumerate(clips):
        if not placement.boomerang:
            expanded.append(placement)
            continue
        ctx = f"timeline.tracks[{track_idx}].clips[{c_idx}]"
        forward = replace(placement, boomerang=False)
        expanded.append(forward)
        if placement.speed_ramp is not None:
            errors.append(msg("validation.boomerang_conflicts_with_speed_ramp", ctx=ctx))
            continue
        own_duration_s = _boomerang_source_duration_s(placement)
        if own_duration_s is None:
            errors.append(msg("validation.boomerang_requires_explicit_duration", ctx=ctx))
            continue
        own_duration_frames = fps.seconds_to_frames(own_duration_s)
        reverse_start_frame = fps.seconds_to_frames(placement.start) + own_duration_frames
        expanded.append(
            replace(
                forward,
                start=fps.frames_to_seconds(reverse_start_frame),
                fade_in=None,
                fade_in_level=None,
                mute=True,
                _boomerang_reverse_of=placement.clip,
            )
        )
    return expanded


def _trim_boomerang_holds(
    clips: list[ClipPlacementSpec],
    resolved_bin: dict[str, ResolvedBinClip],
    fps: FrameRate,
    cache_dir: Path,
    errors: list[str],
) -> list[ClipPlacementSpec]:
    """Applies ClipPlacementSpec.boomerang_trim_hold to every (forward, reverse) pair that
    _expand_boomerangs produced on one track: measures how long the window's last picture is held
    and moves both halves' `source_out` back by half of that, then re-derives the reverse half's
    `start` from the forward half's new length (in frames, exactly like _expand_boomerangs does -
    see its docstring for why). Runs here rather than in _expand_boomerangs because measuring the
    hold needs the bin clip's resolved path, which parsing doesn't have yet."""
    adjusted = list(clips)
    for i in range(len(adjusted) - 1):
        forward, reverse = adjusted[i], adjusted[i + 1]
        if reverse._boomerang_reverse_of is None or forward._boomerang_reverse_of is not None:
            continue
        if not forward.boomerang_trim_hold or forward.source_in is None or forward.source_out is None:
            continue
        bin_clip = resolved_bin.get(forward.clip)
        if bin_clip is None or bin_clip.kind in ("image", "title"):
            continue  # unknown clip: reported by the main loop; stills have no frames to hold
        try:
            hold_s = probe.detect_trailing_hold_seconds(
                bin_clip.path, forward.source_in, forward.source_out, cache_dir
            )
        except probe.ProbeError as exc:
            errors.append(str(exc))
            continue
        if hold_s < probe.HOLD_MIN_S:
            continue
        new_out = forward.source_out - hold_s / 2
        forward = replace(forward, source_out=new_out)
        speed = abs(forward.speed) if forward.speed else 1.0
        own_frames = fps.seconds_to_frames(abs(new_out - forward.source_in) / speed)
        reverse = replace(
            reverse,
            source_out=new_out,
            start=fps.frames_to_seconds(fps.seconds_to_frames(forward.start) + own_frames),
        )
        adjusted[i], adjusted[i + 1] = forward, reverse
    return adjusted


def _parse_track(item: Any, idx: int, errors: list[str], fps: FrameRate) -> TrackSpec | None:
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
    clips = _expand_boomerangs(clips, idx, errors, fps)

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
            errors.append(msg("validation.pair_invalid", ctx=ctx, field=key))
            return None
        return (float(val[0]), float(val[1]))

    mute = item.get("mute", False)
    if not isinstance(mute, bool):
        errors.append(msg("validation.mute_invalid", ctx=ctx, mute=mute))
        mute = False

    boomerang = item.get("boomerang", False)
    if not isinstance(boomerang, bool):
        errors.append(msg("validation.boomerang_invalid", ctx=ctx, boomerang=boomerang))
        boomerang = False

    boomerang_trim_hold = item.get("boomerang_trim_hold", True)
    if not isinstance(boomerang_trim_hold, bool):
        errors.append(msg("validation.boomerang_trim_hold_invalid", ctx=ctx, value=boomerang_trim_hold))
        boomerang_trim_hold = True

    source_in = float(item["source_in"]) if item.get("source_in") is not None else None
    source_out = float(item["source_out"]) if item.get("source_out") is not None else None
    if item.get("speed_ramp") is not None and source_in is not None and source_out is None:
        # `source_in:` alone + `speed_ramp:` + `duration:`/`out:` - the ramp
        # starts reading the source at `source_in` real seconds instead of
        # at 0 (see ClipPlacementSpec.source_in). No `source_out:` here: with
        # a ramp, where the source runs out is already fully determined by
        # duration + the ramp's own segments, so a second, independent
        # bound could only ever disagree with it. `in:` stays rejected
        # (speed_ramp_in_not_supported, below) - `source_in` is the one way
        # to offset a ramp, in real source seconds.
        if source_in < 0:
            errors.append(msg("validation.source_in_out_invalid", ctx=ctx, source_in=source_in, source_out="-"))
            source_in = None
    elif (source_in is None) != (source_out is None):
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
            errors.append(msg("validation.quad_invalid", ctx=ctx, field=key))
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
    dust_style, dust_size, dust_count, dust_pictures, dust_hold = "svg", 2, 10, None, 1
    raw_dust = item.get("film_dust")
    if isinstance(raw_dust, dict):
        # {style: svg | dots, size, count, pictures, hold} - see ClipPlacementSpec.film_dust_style
        dust_style = raw_dust.get("style", "svg")
        dust_size = raw_dust.get("size", 2 if dust_style == "svg" else 4)
        dust_count = raw_dust.get("count", 10)
        dust_pictures = raw_dust.get("pictures")
        dust_hold = raw_dust.get("hold", 1)
        ok = not set(raw_dust) - {"style", "size", "count", "pictures", "hold"} and dust_style in ("svg", "dots")
        ok = ok and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (dust_size, dust_count, dust_hold))
        if dust_pictures is not None:
            ok = ok and dust_style == "svg" and isinstance(dust_pictures, list) and bool(dust_pictures)
            ok = ok and all(isinstance(v, str) and v.strip() for v in dust_pictures)
            dust_pictures = tuple(dust_pictures) if ok else None
        if not ok:
            errors.append(msg("validation.film_dust_invalid", ctx=ctx, value=raw_dust))
        item = {**item, "film_dust": True}
    grain_noise, grain_contrast, grain_brightness = 40, 160, 70
    raw_grain = item.get("film_grain")
    if isinstance(raw_grain, dict):
        # {noise, contrast, brightness} - see ClipPlacementSpec.film_grain_noise
        grain_noise = raw_grain.get("noise", 40)
        grain_contrast = raw_grain.get("contrast", 100)
        grain_brightness = raw_grain.get("brightness", 100)
        ok = not set(raw_grain) - {"noise", "contrast", "brightness"}
        ok = ok and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                        for v in (grain_noise, grain_contrast, grain_brightness))
        if not ok:
            errors.append(msg("validation.film_grain_invalid", ctx=ctx, value=raw_grain))
        item = {**item, "film_grain": True}
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

    cg_kf_raw = item.get("color_grade_keyframes")
    color_grade_keyframes: list[tuple[float, dict[str, tuple[float, float, float]]]] | None = None
    if cg_kf_raw is not None:
        parsed_kf = []
        ok = isinstance(cg_kf_raw, list) and bool(cg_kf_raw)
        for kf in cg_kf_raw if ok else ():
            at = kf.get("at") if isinstance(kf, dict) else None
            groups = {k: v for k, v in kf.items() if k != "at"} if isinstance(kf, dict) else {}
            if not (isinstance(at, (int, float)) and not isinstance(at, bool) and at >= 0 and groups):
                ok = False
                break
            parsed = {}
            for key, val in groups.items():
                if key not in ("lift", "gamma", "gain") or not (isinstance(val, (list, tuple)) and len(val) == 3):
                    ok = False
                    break
                try:
                    triple = tuple(float(v) for v in val)
                except (TypeError, ValueError):
                    ok = False
                    break
                if min(triple) < 0:
                    ok = False
                    break
                parsed[key] = triple
            if not ok:
                break
            parsed_kf.append((float(at), parsed))
        if ok:
            ats = [a for a, _ in parsed_kf]
            ok = ats[0] == 0 and all(b > a for a, b in zip(ats, ats[1:]))
            ok = ok and all(set(g) == set(parsed_kf[0][1]) for _, g in parsed_kf)
        if ok:
            color_grade_keyframes = parsed_kf
        else:
            errors.append(msg("validation.color_grade_keyframes_invalid", ctx=ctx))

    stabilize_raw = item.get("stabilize")
    stabilize: bool | None = None
    if stabilize_raw is None:
        pass
    elif not isinstance(stabilize_raw, bool):
        errors.append(msg("validation.bool_field_invalid", ctx=ctx, field="stabilize", value=stabilize_raw))
    else:
        stabilize = stabilize_raw

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
        fade_in_level=float(item["fade_in_level"]) if item.get("fade_in_level") is not None else None,
        fade_out_level=float(item["fade_out_level"]) if item.get("fade_out_level") is not None else None,
        gain=float(item["gain"]) if item.get("gain") is not None else None,
        mute=mute,
        boomerang=boomerang,
        boomerang_trim_hold=boomerang_trim_hold,
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
        film_dust_style=dust_style,
        film_dust_size=dust_size,
        film_dust_count=dust_count,
        film_dust_pictures=dust_pictures,
        film_dust_hold=dust_hold if isinstance(dust_hold, int) else 1,
        film_scratches=old_film_flags["film_scratches"],
        film_grain_noise=grain_noise,
        film_grain_contrast=grain_contrast,
        film_grain_brightness=grain_brightness,
        film_grain=old_film_flags["film_grain"],
        color_grade=color_grade,
        color_grade_keyframes=color_grade_keyframes,
        stabilize=stabilize,
        lut=_parse_lut(item.get("lut"), ctx, errors),
        lut_interp=_parse_lut_interp(item.get("lut_interp", "tetrahedral"), ctx, errors),
        melt_filters=meltpass.parse_filters(item.get("melt"), ctx, errors),
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
) -> WipeTransitionSpec | AffineTransitionSpec | MeltTransitionSpec | None:
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
        return None
    t_type = item["type"]
    is_melt = isinstance(t_type, str) and t_type.startswith(meltpass.PREFIX)
    if t_type not in ("wipe", "affine", "gl") and not is_melt:
        errors.append(msg("validation.transition_type_invalid", ctx=ctx, type=t_type))
        return None

    track_a, track_b = item.get("track_a"), item.get("track_b")
    if track_a not in track_names or track_b not in track_names:
        errors.append(msg("validation.transition_unknown_track", ctx=ctx, track_a=track_a, track_b=track_b))
        return None

    if "start" not in item or "end" not in item:
        errors.append(msg("validation.transition_missing_span", ctx=ctx))
        return None
    start, end = float(item["start"]), float(item["end"])
    if end <= start:
        errors.append(msg("validation.transition_end_not_after_start", ctx=ctx, start=start, end=end))
        return None

    if t_type == "wipe":
        luma = item.get("luma", "linear_x")
        problem = lumas.luma_problem(ctx, luma, "validation.transition_luma_invalid")
        if problem:
            errors.append(problem)
            return None
        softness = lumas.wipe_softness(item.get("softness", 0), ctx, "softness", errors)
        reverse = item.get("reverse", False)
        if not isinstance(reverse, bool):
            errors.append(msg("validation.wipe_reverse_invalid", ctx=ctx, field="reverse", value=reverse))
        if softness is None or not isinstance(reverse, bool):
            return None
        return WipeTransitionSpec(track_a=track_a, track_b=track_b, start=start, end=end, luma=luma, softness=softness, reverse=reverse)

    if t_type == "gl":
        service = gltransitions.parse(item.get("gl"), item.get("gl_params"), ctx, errors)
        if service is None:
            return None
        return MeltTransitionSpec(track_a=track_a, track_b=track_b, start=start, end=end, melt=service)

    if is_melt:
        service = meltpass.parse_service("transition", t_type[len(meltpass.PREFIX):], item.get("properties"), ctx, errors)
        if service is None:
            return None
        return MeltTransitionSpec(track_a=track_a, track_b=track_b, start=start, end=end, melt=service)

    style = item.get("style", "zoom")
    if style not in AFFINE_STYLES:
        errors.append(msg("validation.transition_style_invalid", ctx=ctx, style=style, valid=list(AFFINE_STYLES)))
        return None
    return AffineTransitionSpec(track_a=track_a, track_b=track_b, start=start, end=end, style=style)


def validate_and_resolve(doc: ProjectDoc) -> ResolvedProject:
    errors: list[str] = []
    fps = resolve_fps(doc.project.fps)

    # --- assets: existence on disk -----------------------------------------
    # Derived clips (see derive.py) are generated here if missing or stale
    # and their input is on disk; a missing input is reported by ensure_all.
    lock = sources_lock.read(doc.source_path.parent)
    derive_mod.ensure_all(
        doc.bin,
        doc.source_path.parent / doc.project.sources_dir,
        errors,
        locked_sha256={name: entry["sha256"] for name, entry in lock.items() if entry.get("sha256")},
    )
    bin_by_id: dict[str, BinClipSpec] = {}
    for clip in doc.bin:
        if clip.id in bin_by_id:
            errors.append(msg("validation.bin_duplicate_id", id=clip.id))
            continue
        bin_by_id[clip.id] = clip
        if clip.derive is not None:
            continue  # ensure_all already made sure it exists, or said why not
        if clip.type != "title" and not clip.path.exists():
            if clip.source is not None or clip.url is not None:
                errors.append(msg("validation.bin_source_not_fetched", id=clip.id, path=clip.path))
            else:
                errors.append(msg("validation.bin_missing_file", id=clip.id, path=clip.path))

    if errors:
        # No point continuing without valid assets (probing would fail in a chain).
        raise ValidationErrors(errors)

    # --- probing: source type and duration ----------------------------------
    titles_dir = doc.source_path.parent / ".titles"
    # See ClipPlacementSpec.boomerang's docstring: reverse halves are
    # rendered here (once, then cached) instead of using MLT's own
    # negative-speed timewarp.
    boomerang_cache_dir = doc.source_path.parent / ".boomerang_cache"
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
                    font_weight=clip.font_weight,
                    italic=clip.italic,
                    outline=clip.outline,
                    outline_color=clip.outline_color,
                    shadow=clip.shadow,
                ),
            )
        elif kind == "image":
            duration_seconds = doc.project.default_image_duration
        elif kind == "sequence":  # a part's timeline (parts.py): as long as it is
            duration_seconds = clip.sequence.total_frames / fps.fps
        elif kind == "animation":  # a Lottie animation: as long as its own frames say
            if not meltpass.is_known("producer", "glaxnimate") and meltpass._services("producer") is not None:
                errors.append(msg("validation.animation_needs_glaxnimate", id=clip_id))
                continue
            try:
                duration_seconds = probe.animation_duration_seconds(clip.path)
            except probe.ProbeError as exc:
                errors.append(str(exc))
                continue
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
            sequence=clip.sequence,
        )

    if errors:
        raise ValidationErrors(errors)

    # --- tracks: resolve in/out/duration and check for overlaps ------------
    resolved_tracks: list[ResolvedTrack] = []
    for t_idx, track in enumerate(doc.tracks):
        placements: list[ResolvedPlacement] = []
        track_clips = _trim_boomerang_holds(track.clips, resolved_bin, fps, boomerang_cache_dir, errors)
        for c_idx, placement in enumerate(track_clips):
            ctx = f"timeline.tracks[{t_idx}]({track.name}).clips[{c_idx}]"
            bin_clip = resolved_bin.get(placement.clip)
            if bin_clip is None:
                errors.append(msg("validation.clip_unknown_reference", ctx=ctx, clip=placement.clip))
                continue

            if placement._boomerang_reverse_of is not None:
                # See ClipPlacementSpec.boomerang's docstring: substitute a
                # pre-reversed derivative for this placement's bin clip
                # instead of resolving `placement.clip` directly, and
                # rewrite it to reference that derivative from its own
                # start (0) at the same speed - real render testing found
                # MLT's own negative-speed timewarp reliably corrupts a
                # chunk of playback on large images, so this sidesteps it
                # entirely rather than trying to fix it at the XML level.
                window = _boomerang_source_window_s(placement)
                if window is None:
                    errors.append(msg("validation.boomerang_requires_explicit_duration", ctx=ctx))
                    continue
                in_win_s, out_win_s = window
                try:
                    reversed_path = probe.render_reversed_segment(
                        bin_clip.path, in_win_s, out_win_s, boomerang_cache_dir
                    )
                except probe.ProbeError as exc:
                    errors.append(str(exc))
                    continue
                segment_len_s = out_win_s - in_win_s
                # `duration_frames` below feeds `source_duration_s = frames_to_seconds(round(
                # duration_frames / divisor))` a few lines down, checked against `out_s =
                # source_out / divisor` (computed directly from the unrounded segment_len_s, no
                # intermediate frame-rounding). Dividing an already-rounded frame count by a
                # non-integer speed can round DOWN relative to dividing the real seconds value
                # first (confirmed on real-world clips - e.g. segment_len_s=3.0s
                # @ speed=1.98, lost ~0.015s, most of a frame at 30fps, this way), which would
                # otherwise fail the "out exceeds duration" check for a derivative file that's
                # actually exactly the right length.
                #
                # Fix: pick the smallest frame count whose frames_to_seconds is >= out_s (a
                # ceiling, NOT `fps.seconds_to_frames`'s round-half-up - out_s often isn't exactly
                # on a frame boundary, and rounding it to the *nearest* frame can round down,
                # reproducing the exact same problem one level up - confirmed on a real clip:
                # out_s=0.766936s rounds to 23 frames = 0.766667s, which is already < out_s).
                # Then solve for a `duration_frames` guaranteed to round back to at least that
                # many frames regardless of `speed`: since out_frames is an integer,
                # D/divisor >= out_frames implies round(D/divisor) >= out_frames (round only ever
                # moves a value by <=0.5, so it can't drop below the next integer below an
                # already->= value) - ceil() plus one whole extra frame of margin for float
                # precision at that exact boundary.
                divisor = abs(placement.speed) if placement.speed else 1.0
                out_s = segment_len_s / divisor
                out_frames = math.ceil(out_s * fps.num / fps.den - 1e-9)
                bin_clip = ResolvedBinClip(
                    id=bin_clip.id,
                    numeric_id=bin_clip.numeric_id,
                    path=reversed_path,
                    kind=bin_clip.kind,
                    duration_frames=math.ceil((out_frames + 1) * divisor),
                    folder=bin_clip.folder,
                )
                placement = replace(placement, source_in=0.0, source_out=segment_len_s)

            speed = placement.speed
            if speed is not None and bin_clip.kind in ("image", "title"):
                errors.append(msg("validation.speed_on_image", ctx=ctx, clip=placement.clip))
                speed = None

            speed_ramp = placement.speed_ramp
            if speed_ramp is not None and bin_clip.kind in ("image", "title"):
                errors.append(msg("validation.speed_ramp_on_image", ctx=ctx, clip=placement.clip))
                speed_ramp = None

            ramp_source_offset_s = 0.0
            if speed_ramp is not None:
                # Note: the `source_in`+`source_out` PAIR combined with
                # `speed_ramp` is already rejected earlier - speed_ramp
                # requires an explicit out:/duration: (below), and the
                # pair rejects being combined with in:/out:/duration: (see
                # the source_in_out_conflicts check). `source_in` ALONE is
                # the one supported way to offset a ramp (see
                # _parse_clip_placement): it only shifts where in the real
                # source the ramp starts reading - see xmlgen.py's
                # _speed_ramp_time_map.
                # Still narrow scope otherwise (see
                # ClipPlacementSpec.speed_ramp): the placement's own range
                # always starts at 0 (in_s forced to 0 - already validated
                # in _parse_clip_placement that `in:` isn't set alongside a
                # ramp), and needs an explicit out:/duration: - no
                # auto-inferred "use all available source" default like
                # flat `speed:` gets, since that would need solving for
                # where a multi-segment ramp runs out of source material
                # rather than a single division.
                ramp_source_offset_s = placement.source_in or 0.0
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
                # The ramp starts reading at source_in, so whatever comes
                # before it isn't available to consume.
                available_source_s = fps.frames_to_seconds(bin_clip.duration_frames) - ramp_source_offset_s
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
            fade_in_level = placement.fade_in_level if placement.fade_in_level is not None else 0.0
            fade_out_level = placement.fade_out_level if placement.fade_out_level is not None else 0.0
            if fade_in_s < 0 or fade_out_s < 0:
                errors.append(msg("validation.fade_negative", ctx=ctx))
            if placement.fade_in_level is not None and not (0.0 <= placement.fade_in_level <= 1.0):
                errors.append(msg("validation.fade_in_level_out_of_range", ctx=ctx, fade_in_level=placement.fade_in_level))
            if placement.fade_out_level is not None and not (0.0 <= placement.fade_out_level <= 1.0):
                errors.append(msg("validation.fade_out_level_out_of_range", ctx=ctx, fade_out_level=placement.fade_out_level))
            if placement.gain is not None and placement.gain < 0:
                errors.append(msg("validation.gain_negative", ctx=ctx, gain=placement.gain))
            # Independent checks (not elif off the level checks above): an out-of-range level and
            # fades that don't fit the clip are unrelated problems, so both should be reported
            # together instead of the second check being skipped when the first already failed.
            if fade_in_s + fade_out_s > clip_len_s + 1e-6:
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
                    fade_in_level=fade_in_level,
                    fade_out_level=fade_out_level,
                    gain=placement.gain,
                    mute=placement.mute,
                    speed=speed,
                    preserve_pitch=placement.preserve_pitch,
                    speed_ramp=speed_ramp_frames,
                    speed_ramp_image_mode=placement.speed_ramp_image_mode,
                    speed_ramp_source_offset_s=ramp_source_offset_s,
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
                    color_grade_keyframes=None if placement.color_grade_keyframes is None else [
                        (fps.seconds_to_frames(at), groups) for at, groups in placement.color_grade_keyframes
                    ],
                    old_film=placement.old_film,
                    film_dust=placement.film_dust,
                    film_dust_style=placement.film_dust_style,
                    film_dust_size=placement.film_dust_size,
                    film_dust_count=placement.film_dust_count,
                    film_dust_hold=placement.film_dust_hold,
                    film_dust_pictures=_dust_pictures(placement.film_dust_pictures, doc.source_path.parent, ctx, errors),
                    film_scratches=placement.film_scratches,
                    film_grain_noise=placement.film_grain_noise,
                    film_grain_contrast=placement.film_grain_contrast,
                    film_grain_brightness=placement.film_grain_brightness,
                    film_grain=placement.film_grain,
                    stabilize=placement.stabilize,
                    lut=_lut_file(placement.lut, doc.source_path.parent, ctx, errors),
                    lut_interp=placement.lut_interp,
                    melt_filters=placement.melt_filters,
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

    # dust drawn as dots or as pictures is one choice for the whole render
    # (melt's data folder, see mltdata.py), not per clip
    dust_styles = {p.film_dust_style for t in resolved_tracks for p in t.placements if p.film_dust}
    if len(dust_styles) > 1:
        errors.append(msg("validation.film_dust_mixed_styles"))
    dust_pictures = {p.film_dust_pictures for t in resolved_tracks for p in t.placements if p.film_dust}
    if len(dust_pictures) > 1:
        errors.append(msg("validation.film_dust_mixed_pictures"))
    if errors:
        raise ValidationErrors(errors)

    resolved_wipes = [
        ResolvedWipeTransition(
            track_a=w.track_a,
            track_b=w.track_b,
            start_frame=fps.seconds_to_frames(w.start),
            end_frame=fps.seconds_to_frames(w.end) - 1,
            luma=w.luma,
            softness=w.softness,
            reverse=w.reverse,
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
    resolved_melt_transitions = [
        ResolvedMeltTransition(
            track_a=t.track_a,
            track_b=t.track_b,
            start_frame=fps.seconds_to_frames(t.start),
            end_frame=fps.seconds_to_frames(t.end) - 1,
            melt=t.melt,
        )
        for t in doc.melt_transitions
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
        melt_transitions=resolved_melt_transitions,
        guides=resolved_guides,
        notes=doc.project.notes,
    )
