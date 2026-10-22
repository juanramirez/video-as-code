"""`derive: {openshot: [...]}`: libopenshot's effects - Wave, AnalogTape,
LensFlare, SphericalProjection, Pixelate, ColorShift... and its audio
ones (Echo, Robotization...) - applied to a clip when it's generated:

    - id: wavy
      derive:
        path: media/sea.mp4
        openshot:
          - {effect: Wave, amplitude: 0.6, wavelength: {0: 0.06, 3: 0.2}}
          - {effect: AnalogTape}

An effect is a map with its name and any of its properties: a number
(held all along), a map of seconds to numbers (keyframes, linear in
between), a colour ([r, g, b] or "#rrggbb"), true/false or text. Applied
in order, after derive:'s other operations (derive.py), by libopenshot
itself.

libopenshot's Python bindings (python3-openshot, which openshot-qt
brings) are built for the system's Python, which needn't be the one
video-as-code runs on - and mixing its Qt 5 with MLT's Qt 6 in one
process isn't an option either - so the rendering runs in a process of
its own (openshot_render.py, run with that Python), as Real-ESRGAN does
for upscale. Which Python: VIDEO_AS_CODE_OPENSHOT_PYTHON, else
/usr/bin/python3 if it can import openshot. Its catalog of effects is
asked once per libopenshot version and kept in the user cache.
"""
from __future__ import annotations

import difflib
import functools
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from .errors import KdenliveAsCodeError
from .i18n import msg
from .mltdata import default_cache_root

PYTHON_ENV = "VIDEO_AS_CODE_OPENSHOT_PYTHON"
_SCRIPT = Path(__file__).parent / "openshot_render.py"


# effects that can't work in a render of ours: the message saying why
UNSUPPORTED = {"Caption": "validation.openshot_caption", "Mask": "validation.openshot_mask"}


class OpenShotError(KdenliveAsCodeError):
    pass


@functools.lru_cache(maxsize=None)
def python() -> Optional[str]:
    """A Python that can import openshot, or None."""
    candidate = os.environ.get(PYTHON_ENV) or "/usr/bin/python3"
    try:
        ok = subprocess.run([candidate, "-I", "-c", "import openshot"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None
    return candidate if ok else None


@functools.lru_cache(maxsize=None)
def catalog() -> dict[str, dict]:
    """effect -> {props: {name: kind}, description, audio}; {} without libopenshot."""
    exe = python()
    if exe is None:
        return {}
    version = subprocess.run([exe, "-I", "-c", "import openshot; print(openshot.OPENSHOT_VERSION_FULL)"],
                             capture_output=True, text=True).stdout.strip()
    cache = default_cache_root() / "openshot" / f"effects-{version}.json"
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    result = subprocess.run([exe, "-I", str(_SCRIPT), "--describe"], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return {}
    data = json.loads(result.stdout)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def _value_ok(kind: str, value: Any) -> bool:
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if kind == "number":
        return number(value) or (isinstance(value, dict) and bool(value) and all(number(v) for v in value.values())
                                 and all(number(t) or (isinstance(t, str) and t.replace(".", "", 1).isdigit()) for t in value))
    if kind == "color":
        return (isinstance(value, str) and len(value) == 7 and value.startswith("#")) or (
            isinstance(value, (list, tuple)) and len(value) in (3, 4) and all(isinstance(v, int) and 0 <= v <= 255 for v in value))
    if kind == "bool":
        return isinstance(value, bool)
    return isinstance(value, str)


def parse(raw: Any, ctx: str, errors: list[str]) -> tuple[str, ...]:
    """derive:'s `openshot:` list, checked against libopenshot's own
    catalog - each effect as JSON, for the spec (its key) and the render."""
    if not isinstance(raw, list) or not raw:
        errors.append(msg("validation.openshot_list_invalid", ctx=ctx))
        return ()
    effects = catalog()
    if not effects:
        errors.append(msg("validation.openshot_missing", ctx=ctx, env=PYTHON_ENV))
        return ()
    out = []
    for i, item in enumerate(raw):
        where = f"{ctx}.openshot[{i}]"
        name = item.get("effect") if isinstance(item, dict) else None
        if name not in effects:
            close = difflib.get_close_matches(str(name), effects, n=3)
            errors.append(msg("validation.openshot_effect_unknown", ctx=where, name=name, close=", ".join(close) or "-"))
            continue
        if name in UNSUPPORTED:
            errors.append(msg(UNSUPPORTED[name], ctx=where))
            continue
        props = effects[name]["props"]
        for key, value in item.items():
            if key == "effect":
                continue
            if key not in props:
                close = difflib.get_close_matches(key, props, n=3)
                errors.append(msg("validation.openshot_property_unknown", ctx=where, effect=name, name=key,
                                  close=", ".join(close) or "-", valid=", ".join(sorted(props))))
            elif not _value_ok(props[key], value):
                errors.append(msg("validation.openshot_value_invalid", ctx=where, name=key, value=value, kind=props[key]))
        out.append(json.dumps({"effect": name, **{k: v for k, v in item.items() if k != "effect"}}, sort_keys=True))
    return tuple(out)


def render(clip_id: str, effects: tuple[str, ...], source: Path, output: Path) -> None:
    exe = python()
    if exe is None:
        raise OpenShotError(msg("validation.openshot_missing", ctx=clip_id, env=PYTHON_ENV))
    payload = json.dumps([json.loads(e) for e in effects])
    try:
        result = subprocess.run([exe, "-I", str(_SCRIPT), str(source), str(output), payload],
                                capture_output=True, text=True, errors="replace", cwd=output.parent, timeout=3600)
    except subprocess.TimeoutExpired as exc:
        raise OpenShotError(msg("derive.openshot_failed", id=clip_id, stderr="timed out")) from exc
    if result.returncode != 0 or not output.exists():
        output.unlink(missing_ok=True)
        raise OpenShotError(msg("derive.openshot_failed", id=clip_id, stderr=(result.stderr or result.stdout).strip()[-2000:]))
