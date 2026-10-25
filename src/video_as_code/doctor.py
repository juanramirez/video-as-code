"""`video-as-code doctor`: what this machine has of what video-as-code
uses - melt, ffmpeg and the MLT modules each feature needs, the plugins
it builds, the optional tools - and, for what's missing, how to get it.

melt's own modules are found the way a render would find them: by
asking that melt (`-query producers`/`filters`/`transitions`), in the
environment it renders in (probe.melt_env: FREI0R_PATH with this
project's own plugins in it). A module missing from a melt doesn't stop
it starting: the clips using it render as nothing, silently - the reason
this command is worth running on a new machine.
"""
from __future__ import annotations

import functools
import importlib.util
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import gltransitions, glfilters, openshot_effects, probe, splitrender, upscale, watch
from .i18n import msg


@dataclass(frozen=True)
class Check:
    what: str  # what it is, and what needs it
    ok: bool
    detail: str = ""  # a version, a path - or what's wrong
    fix: str = ""  # how to get it, when it's missing
    required: bool = False


def _run(cmd: list[str], env: Optional[dict] = None) -> str:
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return run.stdout + run.stderr


def _version(cmd: list[str], word: int) -> str:
    """The word-th word of the first line `cmd` prints (`melt 7.36.1`: 1)."""
    lines = _run(cmd).strip().splitlines()
    words = lines[0].split() if lines else []
    return words[word] if len(words) > word else ""


@functools.lru_cache(maxsize=None)
def melt_services(melt: str) -> dict[str, frozenset[str]]:
    """{"producers"|"filters"|"transitions": names} of that melt."""
    env = probe.melt_env()
    out = {}
    for kind in ("producers", "filters", "transitions"):
        text = _run([melt, "-query", kind], env=env)
        out[kind] = frozenset(line.strip()[2:] for line in text.splitlines() if line.strip().startswith("- "))
    return out


def _tools() -> list[Check]:
    melt = probe.find_melt()
    checks = [
        Check(msg("doctor.melt"), melt is not None,
              f"{_version([melt, '-version'], 1)} ({melt})" if melt else msg("doctor.not_found"),
              msg("doctor.fix_melt"), required=True),
    ]
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        checks.append(Check(msg(f"doctor.{tool}"), path is not None,
                            _version([tool, "-version"], 2) if path else msg("doctor.not_found"),
                            msg("doctor.fix_ffmpeg"), required=True))
    return checks


# (kind, service, what needs it, how to get it)
_MODULES = [
    ("producers", "kdenlivetitle", "doctor.titles", "doctor.fix_qt"),
    ("transitions", "qtblend", "doctor.qtblend", "doctor.fix_qt"),
    ("producers", "timewarp", "doctor.speed", "doctor.fix_mlt"),
    ("filters", "frei0r.", "doctor.frei0r", "doctor.fix_frei0r"),
    ("filters", "avfilter.lut3d", "doctor.avfilter", "doctor.fix_avfilter"),
    ("filters", "deshake", "doctor.deshake", "doctor.fix_vidstab"),
    ("producers", "glaxnimate", "doctor.glaxnimate", "doctor.fix_glaxnimate"),
]


def _modules(melt: str) -> list[Check]:
    services = melt_services(melt)
    checks = []
    for kind, service, what, fix in _MODULES:
        names = services[kind]
        found = any(n.startswith(service) for n in names) if service.endswith(".") else service in names
        checks.append(Check(msg(what), found, "" if found else msg("doctor.not_in_melt", service=service), msg(fix)))
    return checks


def _plugins(melt: Optional[str]) -> list[Check]:
    """This project's own frei0r plugins (gltransitions.py, glfilters.py)."""
    compiler = shutil.which("cc") or shutil.which("gcc")
    fix = msg("doctor.fix_gl") + ("" if compiler else " " + msg("doctor.fix_gl_compiler"))
    checks = []
    for plugin, service, what in ((gltransitions.PLUGIN, gltransitions.SERVICE, "doctor.gl"),
                                  (glfilters.PLUGIN, "frei0r.glfilter", "doctor.glsl")):
        built = gltransitions.installed(plugin)
        kind = "transitions" if plugin == gltransitions.PLUGIN else "filters"
        loaded = melt is not None and service in melt_services(melt)[kind]
        if not built:
            detail = msg("doctor.gl_not_built")
        elif melt is not None and not loaded:
            detail = msg("doctor.gl_not_loaded", service=service)  # (built, but this melt doesn't see it: no frei0r)
        else:
            detail = str(gltransitions.install_dir() / plugin)
        checks.append(Check(msg(what), built and (melt is None or loaded), detail, fix))
    return checks


def _optional() -> list[Check]:
    openshot = openshot_effects.version()
    esrgan = upscale.find_binary()
    player = watch.player_command(Path("x.mp4"), 0)
    kdenlive = shutil.which("kdenlive")
    reconstruct = all(importlib.util.find_spec(m) for m in ("numpy", "PIL"))
    return [
        Check(msg("doctor.openshot"), openshot is not None, openshot or msg("doctor.not_found"), msg("doctor.fix_openshot")),
        Check(msg("doctor.esrgan"), esrgan is not None, str(esrgan) if esrgan else msg("doctor.esrgan_later"),
              "" if upscale.can_install() else msg("doctor.fix_esrgan")),
        Check(msg("doctor.player"), player is not None, player[0] if player else msg("doctor.not_found"),
              msg("doctor.fix_player", env=watch.PLAYER_ENV)),
        Check(msg("doctor.scope"), splitrender._scope_available(), "" if splitrender._scope_available()
              else msg("doctor.not_found"), msg("doctor.fix_scope")),
        Check(msg("doctor.kdenlive"), kdenlive is not None, kdenlive or msg("doctor.not_found"), msg("doctor.fix_kdenlive")),
        Check(msg("doctor.reconstruct"), reconstruct, "" if reconstruct else msg("doctor.not_found"),
              msg("doctor.fix_reconstruct")),
    ]


def checks() -> list[tuple[str, list[Check]]]:
    """(heading, its checks), in the order shown."""
    melt = probe.find_melt()
    groups = [(msg("doctor.group_needed"), _tools())]
    if melt is not None:
        groups.append((msg("doctor.group_melt", melt=melt), _modules(melt)))
    groups.append((msg("doctor.group_plugins"), _plugins(melt)))
    groups.append((msg("doctor.group_optional"), _optional()))
    return groups


def run(report: Callable[[str], None] = print) -> int:
    """Says what's there and what isn't; 1 if something needed is missing."""
    report(msg("doctor.header", python=platform.python_version(), system=f"{platform.system()} {platform.machine()}"))
    missing_needed = missing = 0
    for heading, group in checks():
        report("")
        report(heading)
        for check in group:
            mark = "✓" if check.ok else ("✗" if check.required else "–")
            report(f"  {mark} {check.what}" + (f": {check.detail}" if check.detail else ""))
            if not check.ok:
                missing += 1
                missing_needed += check.required
                if check.fix:
                    report(f"      → {check.fix}")
    report("")
    if missing_needed:
        report(msg("doctor.needed_missing", n=missing_needed))
    elif missing:
        report(msg("doctor.some_missing", n=missing))
    else:
        report(msg("doctor.all_there"))
    return 1 if missing_needed else 0
