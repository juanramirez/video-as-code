"""`glsl:` on a clip: a GLSL effect on its picture, run on the GPU - one of
the shaders this project brings (gltransitions_data/glsl_filters/: crt,
chromatic, wave, glitch, kaleidoscope, vhs - ours, MIT) or one of the
user's own:

    - clip: beach
      start: 0
      duration: 4
      glsl: crt                                   # with its defaults
    - {clip: city, start: 4, duration: 3, glsl: {shader: wave, params: {amplitude: 0.03}}}
    - {clip: sea, start: 7, duration: 3, glsl: {shader: looks/mine.glsl, params: {amount: 0.5}}}

A shader is GLSL ES 3.00 with `vec4 effect(vec2 uv)`, which reads the
picture with `getColor(uv)` (uv 0..1, bottom-left origin) and has
`time` (seconds into the clip), `progress` (0..1 over it), `ratio`
(width / height) and `resolution` (in pixels) - and its own parameters
as uniforms, each given its value (a bundled shader's defaults filled
in; a file's own as written - a uniform it isn't given is 0).

Rendered by a frei0r filter plugin of this project's, `frei0r.glfilter`
(gltransitions_data/glfilter.c), the single-clip sibling of
gl-transitions' (gltransitions.py) and built with it by
`video-as-code install-gl-transitions`.
"""
from __future__ import annotations

import difflib
import functools
import json
from pathlib import Path
from typing import Any, Optional

from . import gltransitions
from .i18n import msg
from .models import MeltService

SERVICE = "frei0r.glfilter"
PLUGIN = "glfilter.so"
SHADERS_DIR = gltransitions.DATA_DIR / "glsl_filters"


@functools.lru_cache(maxsize=None)
def catalog() -> dict[str, dict]:
    """name -> {description, animated, params: {name: {type, default, min?, max?}}}"""
    return json.loads((SHADERS_DIR / "catalog.json").read_text(encoding="utf-8"))


def shader_path(name: str) -> Path:
    return SHADERS_DIR / f"{name}.glsl"


def installed() -> bool:
    return gltransitions.installed(PLUGIN)


def parse(raw: Any, ctx: str, base: Path, errors: list[str]) -> Optional[MeltService]:
    """`glsl:` as the frei0r.glfilter filter it is - a bundled shader by
    name, or a .glsl file (relative to `base`, the project file's folder)."""
    if isinstance(raw, str):
        raw = {"shader": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("shader"), str) or set(raw) - {"shader", "params"}:
        errors.append(msg("validation.glsl_invalid", ctx=ctx, value=raw))
        return None
    shader, params = raw["shader"], raw.get("params") or {}
    if not isinstance(params, dict):
        errors.append(msg("validation.glsl_params_invalid", ctx=ctx, value=params))
        return None
    if shader.endswith(".glsl"):  # the user's own: its parameters as written
        path = Path(shader) if Path(shader).is_absolute() else base / shader
        if not path.is_file():
            errors.append(msg("validation.glsl_file_missing", ctx=ctx, path=path))
            return None
        values = params
        bad = [k for k, v in values.items() if gltransitions._value_problem({"type": _guess_type(v)}, v)]
        if bad:
            errors.append(msg("validation.glsl_params_invalid", ctx=ctx, value=params))
            return None
    else:
        known = catalog()
        if shader not in known:
            close = difflib.get_close_matches(shader, known, n=3)
            errors.append(msg("validation.glsl_unknown", ctx=ctx, name=shader, close=", ".join(close) or "-",
                              known=", ".join(known)))
            return None
        path = shader_path(shader)
        specs = known[shader]["params"]
        ok = True
        for key, value in params.items():
            if key not in specs:
                errors.append(msg("validation.gl_param_unknown", ctx=ctx, name=shader, param=key, valid=", ".join(specs) or "-",
                                  close=", ".join(difflib.get_close_matches(str(key), specs, n=3)) or "-"))
                ok = False
            elif gltransitions._value_problem(specs[key], value):
                errors.append(msg("validation.gl_param_invalid", ctx=ctx, param=key, value=value,
                                  expected=gltransitions._describe(specs[key])))
                ok = False
        if not ok:
            return None
        values = {key: params.get(key, spec.get("default")) for key, spec in specs.items()}
    if not installed():
        errors.append(msg("validation.glsl_not_installed", ctx=ctx))
        return None
    uniforms = " ".join(f"{k}:{gltransitions._format(v)}" for k, v in values.items() if v is not None)
    return MeltService(SERVICE, (("shader", str(path.resolve())), ("progress", "0=0;-1=1"), ("uniforms", uniforms)))


def _guess_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (list, tuple)):
        return {2: "vec2", 3: "vec3", 4: "vec4"}.get(len(value), "?")
    return "float"


def from_properties(props: dict[str, str]) -> Optional[dict]:
    """The `glsl:` a frei0r.glfilter filter came from (reverse.py)."""
    shader = Path(props.get("shader", ""))
    params: dict[str, Any] = {}
    for item in props.get("uniforms", "").split():
        key, _, text = item.partition(":")
        try:
            numbers = [float(t) for t in text.split(",")]
        except ValueError:
            return None
        params[key] = numbers[0] if len(numbers) == 1 else numbers
    if shader.parent.resolve() == SHADERS_DIR.resolve() and shader.stem in catalog():
        specs = catalog()[shader.stem]["params"]
        params = {k: v for k, v in params.items() if v != specs.get(k, {}).get("default")}
        name = shader.stem
    else:
        name = str(shader)
    return {"shader": name, **({"params": params} if params else {})} if params else name
