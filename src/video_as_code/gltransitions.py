"""`transition: gl` (`gl: <name>`, `gl_params: {...}`): the 125
transitions of gl-transitions (https://gl-transitions.com) - a cube
turning, a page curling, a morph, ripples... - each a GLSL shader that
mixes the two clips, run on the GPU.

MLT doesn't run GLSL of its own, so this project brings a small frei0r
plugin that does (gltransitions_data/gltransition.c): a "mixer2" plugin,
which MLT takes as a transition, `frei0r.gltransition`, that renders the
shader with EGL and GLES 3 and no window - in the same melt pass as
everything else, and as an ordinary transition in the .kdenlive. It's
built on the user's machine by `video-as-code install-gl-transitions`
(a C compiler and the EGL, GLES and frei0r headers needed: libegl-dev,
libgles-dev and frei0r-plugins-dev on Debian/Ubuntu) into
<XDG data dir>/video-as-code/frei0r/, which probe.melt_env puts first on
FREI0R_PATH. A newer plugin source rebuilds on the next install.

The shaders are bundled (gltransitions_data/glsl3/, their GLSL ES 3.00
form, from the gl-transitions 1.76.1 npm package), with the collection's
own metadata trimmed to what's used here (catalog.json: author, license,
description, and each parameter's type, default and range): they're MIT
(two BSD), see gltransitions_data/LICENSE and each file's header.

The transition is emitted as `frei0r.gltransition` with the shader's
path, `progress` keyframed 0 to 1 over the window, and every parameter -
the defaults included, since a uniform the shader isn't given is 0, not
its documented default.
"""
from __future__ import annotations

import difflib
import functools
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import KdenliveAsCodeError
from .i18n import msg
from .models import MeltService

DATA_DIR = Path(__file__).parent / "gltransitions_data"
SERVICE = "frei0r.gltransition"
PLUGIN = "gltransition.so"
_SOURCE = DATA_DIR / "gltransition.c"
_VECTOR_SIZES = {"vec2": 2, "vec3": 3, "vec4": 4, "ivec2": 2, "ivec3": 3, "ivec4": 4}


class GlTransitionsError(KdenliveAsCodeError):
    pass


@functools.lru_cache(maxsize=None)
def catalog() -> dict[str, dict]:
    """name -> {author, license, description, params: {name: {type, default, min?, max?}}}"""
    return json.loads((DATA_DIR / "catalog.json").read_text(encoding="utf-8"))["transitions"]


def shader_path(name: str) -> Path:
    return DATA_DIR / "glsl3" / f"{name}.glsl"


def install_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "video-as-code" / "frei0r"


# the plugins built from this package: the transition, and its
# single-clip sibling (glfilters.py)
PLUGINS = {PLUGIN: _SOURCE, "glfilter.so": DATA_DIR / "glfilter.c"}


def _source_hash(source: Path = _SOURCE) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _stamp(plugin: Path) -> Path:
    return plugin.with_name(plugin.name + ".source-sha256")


def installed(plugin_name: str = PLUGIN) -> bool:
    """Whether a plugin is built, from its current source."""
    plugin = install_dir() / plugin_name
    try:
        return plugin.is_file() and _stamp(plugin).read_text().strip() == _source_hash(PLUGINS[plugin_name])
    except OSError:
        return False


def install(report: Callable[[str], None] = lambda text: None) -> Path:
    """Builds the plugins (PLUGINS) into install_dir(); returns the folder."""
    compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        raise GlTransitionsError(msg("gltransitions.compiler_missing"))
    target_dir = install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, source in PLUGINS.items():
        plugin = target_dir / name
        report(msg("gltransitions.building", path=plugin))
        with tempfile.TemporaryDirectory(dir=target_dir) as tmp:
            built = Path(tmp) / name
            result = subprocess.run(
                [compiler, "-shared", "-fPIC", "-O2", str(source), "-o", str(built), "-lEGL", "-lGLESv2", "-lpthread"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise GlTransitionsError(msg("gltransitions.build_failed", stderr=result.stderr.strip()))
            built.replace(plugin)
        _stamp(plugin).write_text(_source_hash(source) + "\n")
        report(msg("gltransitions.installed", path=plugin))
    return target_dir


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return ",".join(_format(v) for v in value)
    if isinstance(value, float):
        return format(value, ".10g")
    return str(value)


def _value_problem(spec: dict, value: Any) -> bool:
    """Whether `value` doesn't fit a parameter of this `spec`."""
    kind = spec["type"]
    if kind == "bool":
        return not isinstance(value, bool)
    if kind in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return True
        if kind == "int" and not float(value).is_integer():
            return True
        return ("min" in spec and value < spec["min"]) or ("max" in spec and value > spec["max"])
    size = _VECTOR_SIZES.get(kind)
    if size is None:
        return True
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return True
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return True
    return kind.startswith("i") and any(not float(v).is_integer() for v in value)


def _describe(spec: dict) -> str:
    """Its GLSL type, and range if it has one: `float 0..1`, `vec2`, `bool`."""
    text = spec["type"]
    if "min" in spec or "max" in spec:
        text += f" {spec.get('min', '')}..{spec.get('max', '')}"
    return text


def parse(name: Any, params: Any, ctx: str, errors: list[str]) -> Optional[MeltService]:
    """The `frei0r.gltransition` for `gl: name` with `gl_params: params`."""
    known = catalog()
    if not isinstance(name, str) or name not in known:
        close = difflib.get_close_matches(str(name), known, n=3)
        errors.append(msg("validation.gl_unknown", ctx=ctx, name=name, close=", ".join(close) or "-"))
        return None
    if params is None:
        params = {}
    if not isinstance(params, dict):
        errors.append(msg("validation.gl_params_invalid", ctx=ctx, value=params))
        return None
    specs = known[name]["params"]
    ok = True
    for key, value in params.items():
        if key not in specs:
            close = difflib.get_close_matches(str(key), specs, n=3)
            errors.append(msg("validation.gl_param_unknown", ctx=ctx, name=name, param=key, valid=", ".join(specs) or "-",
                              close=", ".join(close) or "-"))
            ok = False
        elif _value_problem(specs[key], value):
            errors.append(msg("validation.gl_param_invalid", ctx=ctx, param=key, value=value, expected=_describe(specs[key])))
            ok = False
    if not installed():
        errors.append(msg("validation.gl_not_installed", ctx=ctx))
        ok = False
    if not ok:
        return None
    values = {key: params.get(key, spec.get("default")) for key, spec in specs.items()}
    # (not "name=v;...": MLT would read that as keyframes, see gltransition.c)
    uniforms = " ".join(f"{key}:{_format(value)}" for key, value in values.items() if value is not None)
    return MeltService(SERVICE, (("shader", str(shader_path(name))), ("progress", "0=0;-1=1"), ("uniforms", uniforms)))


def from_properties(props: dict[str, str]) -> Optional[tuple[str, dict]]:
    """(`gl:`, `gl_params:` that differ from the defaults) for the
    properties of a frei0r.gltransition this project wrote (reverse.py);
    None for any other."""
    shader = Path(props.get("shader", ""))
    name = shader.stem
    if shader.parent.name != "glsl3" or shader.parent.parent.name != DATA_DIR.name or name not in catalog():
        return None
    specs = catalog()[name]["params"]
    params: dict[str, Any] = {}
    for item in props.get("uniforms", "").split():
        key, _, text = item.partition(":")
        spec = specs.get(key)
        if spec is None:
            return None
        try:
            numbers = [float(t) for t in text.split(",")]
        except ValueError:
            return None
        kind = spec["type"]
        if kind == "bool":
            value: Any = bool(numbers[0])
        elif kind in ("float", "int"):
            value = int(numbers[0]) if kind == "int" else numbers[0]
        else:
            value = [int(n) for n in numbers] if kind.startswith("i") else numbers
        if value != spec.get("default"):
            params[key] = value
    return name, params
