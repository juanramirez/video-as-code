"""The project YAML as a JSON Schema: an editor that knows it (VS Code
with Red Hat's YAML extension, any editor with yaml-language-server)
completes keys and values as they're typed, shows what each one means
and underlines a typo or a wrong value then - not when `validate` or
`render` runs. A file says it follows the schema in its first line:

    # yaml-language-server: $schema=https://video-as-code.juan-ramirez.es/project.schema.json

The schema is the shape schema.py's parsing accepts, written out here
key by key (it parses by hand: there's no model to derive it from), and
each key's meaning taken from docs/project-yaml.md's tables, so the two
don't drift apart. Then the bundled catalogs: the wipe shapes, the
gl-transitions and their parameters, the GLSL shaders and theirs. What
depends on the machine - melt's services, libopenshot's effects,
OpenShot's wipe shapes - is checked by `validate` alone.

It's generated once and shipped (project.schema.json, next to this):

    python -m video_as_code.yamlschema      # after changing either

tests/test_yamlschema.py fails while the shipped one is out of date.
Only what schema.py checks *as it reads a key* is here; whether a file
exists, a clip's `in`/`out` fits its source, two clips overlap... is
still `validate`'s.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import glfilters, gltransitions, lumas
from .derive import FIT_BACKGROUNDS
from .meltpass import PREFIX as MELT_PREFIX
from .models import AFFINE_STYLES, LUT_INTERPS, VALID_CLIP_TYPES, VALID_TRACK_TYPES
from .parts import _BETWEEN_PARTS
from .scenes import BURST_TRANSITIONS, PACING_PRESETS
from .schema import FONT_WEIGHTS, RESERVED_TOP_LEVEL_KEYS
from .upscale import FACTOR as UPSCALE_FACTOR

URL = "https://video-as-code.juan-ramirez.es/project.schema.json"
SHIPPED = Path(__file__).parent / "project.schema.json"
REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "project-yaml.md"  # (in a checkout of the repo)


# --- the reference's tables -------------------------------------------------

_KEY = re.compile(r"`([a-z_]+):?`")


def reference_tables(text: str) -> dict[str, dict[str, str]]:
    """heading -> key -> what the reference says of it: its meaning, and
    its default or example if the table has one. A key in two rows (the
    `as a map` ones) gets both."""
    out: dict[str, dict[str, str]] = {}
    heading, columns, fenced = "", None, False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"#{2,3} (.+)", line)
        if m:
            heading, columns = m.group(1).strip(), None
            continue
        if not line.startswith("|"):
            columns = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if columns is None:
            columns = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(columns, cells))
        first = cells[0]
        keys = _KEY.findall(first)  # (`type: title` isn't one: a form)
        if not keys:
            continue
        text_ = cells[-1]
        extra = row.get("default") or row.get("example")
        if extra and extra != "-":
            label = "Default" if "default" in row else "Example"
            text_ += f"\n\n{label}: {extra}"
        if " as a map" in first:
            text_ = f"As a map: {text_}"
        for key in keys:
            known = out.setdefault(heading, {})
            known[key] = f"{known[key]}\n\n{text_}" if key in known else text_
    return out


# --- building blocks --------------------------------------------------------

NUMBER = {"type": "number"}
BOOL = {"type": "boolean"}
STRING = {"type": "string"}
NAME = {"type": ["string", "number"]}  # (an id: read with str())


def _number(minimum=None, maximum=None, above=None, below=None, integer=False) -> dict:
    out: dict = {"type": "integer" if integer else "number"}
    for key, value in (("minimum", minimum), ("maximum", maximum), ("exclusiveMinimum", above), ("exclusiveMaximum", below)):
        if value is not None:
            out[key] = value
    return out


def _array(items: dict, n: int | None = None, min_items: int | None = None) -> dict:
    out: dict = {"type": "array", "items": items}
    if n is not None:
        out["minItems"] = out["maxItems"] = n
    elif min_items is not None:
        out["minItems"] = min_items
    return out


def _enum(values) -> dict:
    return {"enum": list(values)}


RGBA = _array(_number(0, 255, integer=True), 4)
PAIR = _array(NUMBER, 2)
QUAD = _array(NUMBER, 4)
UNIT = _number(0, 1)
MELT_NAME = {"type": "string", "pattern": f"^{re.escape(MELT_PREFIX)}."}


def _ref(name: str) -> dict:
    return {"$ref": f"#/definitions/{name}"}


def _described(schema: dict, text: str | None) -> dict:
    """`schema` with `text` as its description (a $ref wrapped: draft-07
    ignores what's next to one)."""
    if not text:
        return schema
    if "$ref" in schema:
        schema = {"allOf": [schema]}
    return {**schema, "description": text, "markdownDescription": text}


def _object(props: dict[str, dict], docs: dict[str, str], required=(), rules=()) -> dict:
    """A map of exactly these keys (and `use:`), described from `docs`.
    With `use:` the required ones may come from its presets."""
    props = {"use": _ref("use"), **props}
    out: dict = {
        "type": "object",
        "properties": {k: _described(v, docs.get(k)) for k, v in props.items()},
        "additionalProperties": False,
    }
    if required:
        out["if"] = {"not": {"required": ["use"]}}
        out["then"] = {"required": list(required)}
    if rules:
        out["allOf"] = list(rules)
    return out


def _gl_param(spec: dict) -> dict:
    kind = spec["type"]
    if kind == "bool":
        out = dict(BOOL)
    elif kind in ("float", "int"):
        out = _number(spec.get("min"), spec.get("max"), integer=kind == "int")
    else:
        size = gltransitions._VECTOR_SIZES[kind]
        out = _array(_number(integer=kind.startswith("i")), size)
    if "default" in spec:
        out["default"] = spec["default"]
    text = spec.get("description")
    return _described(out, text) if text else out


def _params(params: dict) -> dict:
    return {"type": "object", "properties": {k: _gl_param(v) for k, v in params.items()}, "additionalProperties": False}


def _per_name(name_key: str, params_key: str, catalog: dict[str, dict]) -> list[dict]:
    """For each name in `catalog`: with `name_key` that name, `params_key`
    takes its parameters."""
    return [
        {"if": {"properties": {name_key: {"const": name}}, "required": [name_key]},
         "then": {"properties": {params_key: _params(entry["params"])}}}
        for name, entry in catalog.items()
    ]


# --- the schema ---------------------------------------------------------------


def build(reference: str) -> dict:
    docs = reference_tables(reference)
    gl = gltransitions.catalog()
    shaders = glfilters.catalog()
    clip_docs = {"clip": "The bin entry it plays, by its `id`.", **docs.get("Clips on a track", {})}

    definitions: dict[str, dict] = {}

    definitions["use"] = _described(
        {"anyOf": [STRING, _array(STRING, min_items=1)]},
        docs.get("`presets:`, `presets_file:` and `use:`", {}).get("use"),
    )
    definitions["source"] = {"anyOf": [
        STRING,
        {"type": "object", "required": ["name"], "additionalProperties": False, "properties": {
            "name": STRING,
            "taken": _described(STRING, "Its capture time, local, to the second: `\"2015-03-08 15:23:51\"`."),
            "size": _described(_array(_number(above=0, integer=True), 2), "Its size in pixels, `[w, h]`."),
            "id": _described(STRING, "The album item's id: its `media_id` in `sources.lock.yaml`."),
        }},
    ]}
    definitions["luma"] = {"anyOf": [
        _enum(lumas.AVAILABLE_LUMAS),
        {"type": "string", "pattern": f"^{re.escape(lumas.OPENSHOT_PREFIX)}[A-Za-z0-9][A-Za-z0-9_-]*$"},
    ]}
    definitions["gl"] = {"enum": sorted(gl, key=str.lower)}
    definitions["glParams"] = {"type": "object"}  # (narrowed per transition by the rules)
    definitions["meltProperties"] = {
        "type": "object",
        "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
    }
    definitions["melt"] = _array({"anyOf": [
        STRING,
        {"type": "object", "minProperties": 1, "maxProperties": 1,
         "additionalProperties": {"anyOf": [{"type": "null"}, _ref("meltProperties")]}},
    ]})
    definitions["glsl"] = {"anyOf": [
        {"anyOf": [_enum(shaders), {"type": "string", "pattern": r"\.glsl$"}]},
        {"type": "object", "required": ["shader"], "additionalProperties": False,
         "properties": {
             "shader": {"anyOf": [_enum(shaders), {"type": "string", "pattern": r"\.glsl$"}]},
             "params": {"type": "object", "additionalProperties": {"anyOf": [NUMBER, BOOL, _array(NUMBER)]}},
         },
         "allOf": _per_name("shader", "params", shaders)},
    ]}
    definitions["lut"] = {"type": "string"}
    definitions["triple"] = _array(_number(0), 3)

    # project:
    sources_docs = docs.get("`project.sources:`", {})
    definitions["sources"] = _object({
        "album": {"anyOf": [STRING, {"type": "object", "additionalProperties": False, "minProperties": 1,
                                     "properties": {"name": STRING, "url": STRING}}]},
        "reference": {"anyOf": [STRING, _array(STRING, min_items=1),
                                {"type": "object", "required": ["url"], "additionalProperties": False,
                                 "properties": {"url": {"anyOf": [STRING, _array(STRING, min_items=1)]}, "path": STRING}}]},
        "dir": STRING,
        "prune_after_days": _number(0),
    }, sources_docs)
    project_docs = dict(docs.get("`project:`", {}))
    definitions["project"] = _object({
        "id": NAME,
        "title": NAME,
        "fps": _number(above=0),
        "width": _number(1, integer=True),
        "height": _number(1, integer=True),
        "colorspace": _number(1, integer=True),
        "default_image_duration": _number(above=0),
        "notes": STRING,
        "sources": _ref("sources"),
    }, project_docs, required=("id", "title", "fps", "width", "height"))

    # bin:
    bin_docs = {"id": "How the timeline (and `derive: {clip}`) refers to it.",
                **docs.get("`bin:`", {}), **docs.get("Title cards (`type: title`)", {})}
    derive_docs = docs.get("`derive:`", {})
    definitions["fit"] = {"type": "object", "required": ["size"], "additionalProperties": False, "properties": {
        "size": _array(_number(1, integer=True), 2),
        "background": _enum(FIT_BACKGROUNDS),
        "lighten": _number(0, below=1),
        "margin": _number(0, integer=True),
        "radius": _number(0, integer=True),
        "edge": _number(0, integer=True),
        "shadow": _number(0, below=1),
        "dim": _number(0, below=1),
    }}
    side = {"anyOf": [{"const": -1}, _number(1, integer=True)]}
    definitions["derive"] = _object({
        "source": _ref("source"),
        "path": STRING,
        "clip": NAME,
        "motion": BOOL,
        "rotate": _enum((90, 180, 270)),
        "trim": _array(_number(0), 2),
        "cut": _array(_array(_number(0), 2), min_items=1),
        "crop": {"type": "object", "required": ["x", "y", "w", "h"], "additionalProperties": False, "properties": {
            "x": _number(0, integer=True), "y": _number(0, integer=True),
            "w": _number(1, integer=True), "h": _number(1, integer=True)}},
        "scale": _array(side, 2),
        "devignette": _number(above=0, below=1),
        "fit": _ref("fit"),
        "feather": _number(1, integer=True),
        "vignette": _number(above=0, below=1),
        "fps": _number(above=0),
        "interpolate": BOOL,
        "reverse": BOOL,
        "volume": _number(0),
        "intra": BOOL,
        "upscale": {"const": UPSCALE_FACTOR},
        "openshot": _array({"type": "object", "required": ["effect"], "properties": {"effect": STRING}}, min_items=1),
    }, derive_docs)
    shadow = {"anyOf": [BOOL, {"type": "object", "additionalProperties": False, "properties": {
        "color": RGBA, "blur": _number(0, integer=True), "offset": _array(_number(integer=True), 2)}}]}
    definitions["binClip"] = _object({
        "id": NAME,
        "type": _enum(VALID_CLIP_TYPES),
        "folder": STRING,
        "path": STRING,
        "source": _ref("source"),
        "url": {"anyOf": [{"type": "string", "pattern": "^https?://"},
                          _array({"type": "string", "pattern": "^https?://"}, min_items=1)]},
        "local_name": STRING,
        "derive": _ref("derive"),
        "text": NAME,
        "font": STRING,
        "font_size": _number(1, integer=True),
        "color": RGBA,
        "background": RGBA,
        "font_weight": {"anyOf": [_number(100, 900, integer=True), _enum(FONT_WEIGHTS)]},
        "italic": BOOL,
        "outline": _number(0),
        "outline_color": RGBA,
        "shadow": shadow,
    }, bin_docs, required=("id",))

    # timeline:
    keyframe = {"type": "object", "required": ["at"], "additionalProperties": False,
                "properties": {"at": _number(0), "lift": _ref("triple"), "gamma": _ref("triple"), "gain": _ref("triple")}}
    film_dust = {"anyOf": [BOOL, {"type": "object", "additionalProperties": False, "properties": {
        "style": _enum(("svg", "dots")),
        "size": _number(1, integer=True),
        "count": _number(1, integer=True),
        "pictures": _array({"type": "string", "minLength": 1}, min_items=1),
        "hold": _number(1, integer=True),
    }}]}
    film_grain = {"anyOf": [BOOL, {"type": "object", "additionalProperties": False, "properties": {
        "noise": _number(0, integer=True), "contrast": _number(0, integer=True), "brightness": _number(0, integer=True)}}]}
    definitions["clip"] = _object({
        "clip": NAME,
        "start": NUMBER,
        "in": NUMBER,
        "out": {"type": ["number", "null"]},
        "duration": {"type": ["number", "null"]},
        "source_in": _number(0),
        "source_out": _number(0),
        "speed": {"type": "number", "not": {"const": 0}},
        "preserve_pitch": BOOL,
        "speed_ramp": _array({"type": "object", "required": ["at", "speed"], "additionalProperties": False,
                              "properties": {"at": NUMBER, "speed": _number(above=0)}}, min_items=1),
        "speed_ramp_image_mode": _enum(("nearest", "blend")),
        "boomerang": BOOL,
        "boomerang_trim_hold": BOOL,
        "freeze_at": _number(0),
        "freeze_before": BOOL,
        "freeze_after": BOOL,
        "fade_in": NUMBER,
        "fade_out": NUMBER,
        "fade_in_level": UNIT,
        "fade_out_level": UNIT,
        "gain": NUMBER,
        "mute": BOOL,
        "crop": {"type": "object", "additionalProperties": False, "properties": {
            k: _number(0) for k in ("top", "right", "bottom", "left")}},
        "position": PAIR,
        "size": PAIR,
        "position_keyframes": _array({"type": "object", "required": ["at", "position", "size"], "additionalProperties": False,
                                      "properties": {"at": NUMBER, "position": PAIR, "size": PAIR}}, min_items=1),
        "ken_burns_from": QUAD,
        "ken_burns_to": QUAD,
        "color_grade": {"type": "object", "minProperties": 1, "additionalProperties": False,
                        "properties": {"lift": _ref("triple"), "gamma": _ref("triple"), "gain": _ref("triple")}},
        "color_grade_keyframes": _array(keyframe, min_items=1),
        "old_film": BOOL,
        "film_dust": film_dust,
        "film_scratches": BOOL,
        "film_grain": film_grain,
        "stabilize": BOOL,
        "lut": _ref("lut"),
        "lut_interp": _enum(LUT_INTERPS),
        "glsl": _ref("glsl"),
        "focus_rect": QUAD,
        "focus_blur": _number(0, 50),
        "chroma_key": STRING,
        "chroma_key_distance": UNIT,
        "reveal": _enum(("iris", "flip", "glitch", "delaygrab", "cairogradient")),
        "overlay_text": STRING,
        "overlay_font": STRING,
        "overlay_font_size": _number(1, integer=True),
        "overlay_color": RGBA,
        "overlay_background": RGBA,
        "melt": _ref("melt"),
    }, clip_docs, required=("clip", "start"))
    definitions["track"] = _object({
        "type": _enum(VALID_TRACK_TYPES),
        "name": STRING,
        "clips": _array(_ref("clip")),
        "pan": _number(-1, 1),
        "hidden": BOOL,
        "locked": BOOL,
        "audio_muted": BOOL,
    }, docs.get("`timeline.tracks:`", {}), required=("type",))

    # scenes:
    definitions["glRules"] = {"allOf": _per_name("gl", "gl_params", gl)}
    gl_rules = [_ref("glRules")]
    scene_docs = docs.get("`scenes:`", {})
    scene_clip_docs = {**clip_docs, "clip": "A bin entry, by its `id`.",
                       "path": "A file (relative to the YAML, or absolute), added to the bin by itself."}
    scene_clip_keys = ("mute", "speed", "preserve_pitch", "position", "size", "chroma_key", "chroma_key_distance",
                       "glsl", "lut", "lut_interp", "melt")
    clip_props = definitions["clip"]["properties"]
    definitions["sceneClip"] = {"anyOf": [
        STRING,
        {**_object({"clip": NAME, "path": STRING, **{k: clip_props[k] for k in scene_clip_keys}}, scene_clip_docs),
         "if": {"not": {"required": ["use"]}}, "then": {"oneOf": [{"required": ["clip"]}, {"required": ["path"]}]}},
    ]}
    scene_transitions = ("none", "dissolve", "wipe", "affine", "iris", "flip", *BURST_TRANSITIONS, "gl")
    transition_keys = {
        "transition_duration": _number(above=0),
        "luma": _ref("luma"),
        "luma_softness": UNIT,
        "luma_reverse": BOOL,
        "style": _enum(AFFINE_STYLES),
        "transition_properties": _ref("meltProperties"),
        "gl": _ref("gl"),
        "gl_params": _ref("glParams"),
    }
    definitions["scene"] = _object({
        "scene": STRING,
        "clips": _array(_ref("sceneClip"), min_items=1),
        "pacing": _enum(PACING_PRESETS),
        "duration": _number(above=0),
        "transition": {"anyOf": [_enum(scene_transitions), MELT_NAME]},
        **transition_keys,
    }, scene_docs, required=("clips",), rules=gl_rules)

    # guides:, transitions:
    definitions["guide"] = _object({
        "at": _number(0),
        "text": STRING,
        "category": {"type": "integer"},
    }, docs.get("`guides:`", {}), required=("at",))
    transitions_docs = docs.get("`transitions:`", {})
    definitions["transition"] = _object({
        "type": {"anyOf": [_enum(("wipe", "affine", "gl")), MELT_NAME]},
        "track_a": STRING,
        "track_b": STRING,
        "start": NUMBER,
        "end": NUMBER,
        "luma": _ref("luma"),
        "softness": UNIT,
        "reverse": BOOL,
        "style": _enum(AFFINE_STYLES),
        "properties": _ref("meltProperties"),
        "gl": _ref("gl"),
        "gl_params": _ref("glParams"),
    }, transitions_docs, required=("type", "track_a", "track_b", "start", "end"), rules=gl_rules)

    # parts:
    parts_docs = docs.get("`parts:` - a project in several files", {})
    part_docs = {**{k: scene_docs[k] for k in transition_keys if k in scene_docs}, **parts_docs}
    definitions["part"] = {"anyOf": [
        STRING,
        _object({
            "file": STRING,
            "transition": {"anyOf": [_enum(_BETWEEN_PARTS), MELT_NAME]},
            **transition_keys,
        }, part_docs, required=("file",), rules=gl_rules),
    ]}

    presets_docs = docs.get("`presets:`, `presets_file:` and `use:`", {})
    top_docs = {
        "project": "Required in a project's main file (a part has none): canvas, frame rate, where sources come from.",
        "bin": "The files (and titles) the video uses.",
        "timeline": "Low level: `tracks:`, every clip placed by hand.",
        "scenes": "High level: lists of clips with their timing worked out.",
        "guides": "Timeline markers.",
        "transitions": "Transitions between two tracks over a time window.",
        "x_kdenlive_raw": "Written by `import`: what it couldn't map, carried back on `build`.",
        "x_reconstruct_unmatched": "Written by `reconstruct`: the stretches it couldn't match, for a person to review.",
        **presets_docs,
        **parts_docs,
    }
    top = _object({
        "project": _ref("project"),
        "bin": _array(_ref("binClip")),
        "timeline": _object({"tracks": _array(_ref("track"))}, {"tracks": "A list of tracks, later ones above earlier ones."}),
        "scenes": _array(_ref("scene")),
        "guides": _array(_ref("guide")),
        "transitions": _array(_ref("transition")),
        "presets": {"type": "object", "additionalProperties": {"type": "object"}},
        "presets_file": STRING,
        "parts": _array(_ref("part"), min_items=1),
        "x_kdenlive_raw": {},
        "x_reconstruct_unmatched": {},
    }, top_docs)
    assert set(top["properties"]) >= RESERVED_TOP_LEVEL_KEYS  # (schema.py's own list)

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": URL,
        "title": "video-as-code project",
        "description": "A video-as-code project file: https://video-as-code.juan-ramirez.es/docs/project-yaml.html",
        **top,
        "definitions": definitions,
    }


def dumps(schema: dict) -> str:
    return json.dumps(schema, indent=1, ensure_ascii=False) + "\n"


def shipped() -> str:
    return SHIPPED.read_text(encoding="utf-8")


def main() -> int:
    if not REFERENCE.is_file():
        print(f"{REFERENCE}: not found (run this from a checkout of the repo)", file=sys.stderr)
        return 1
    SHIPPED.write_text(dumps(build(REFERENCE.read_text(encoding="utf-8"))), encoding="utf-8")
    print(SHIPPED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
