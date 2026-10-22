"""`melt:` on a clip and `melt/<service>` transitions: any MLT filter or
transition, by its service name, with its properties as given - for
what this project has no key of its own for (frei0r's ~100 filters,
ffmpeg's through `avfilter.*`, MLT's own...).

    - clip: beach
      start: 0
      duration: 4
      melt:
        - frei0r.cartoon: {diffspace: 0.4}
        - avfilter.hue: {av.h: "0=0;100=180"}

Each entry becomes one `<filter mlt_service="...">` on the clip, after
every filter of the clip's own keys, in the order given; values are
written as they are (MLT's own units, ranges and keyframe syntax). A
transition (`transition: melt/<service>` on a scene, `type: melt/<service>`
on `transitions:`) becomes an MLT transition between the two tracks over
the window, as a wipe does.

Checked against the melt that would render it, when there is one (it's
what `melt -query` says): an unknown service or property is an error,
not a render that silently does nothing - the way frei0r's filters did
with a melt that couldn't find them (probe.frei0r_dirs). A service melt
lists without describing its properties has them taken on trust. With
no melt at all (a build for kdenlive on another machine), nothing is
checked.
"""
from __future__ import annotations

import difflib
import fnmatch
import functools
import subprocess
from typing import Any, Optional

import yaml

from . import probe
from .i18n import msg
from .models import MeltService

PREFIX = "melt/"
# properties any filter/transition takes, whatever its own are; kdenlive's
# own bookkeeping (kdenlive_id, kdenlive:collapsed...) included
_GENERIC = ("disable", "kdenlive*")


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return format(value, ".10g")
    return str(value)


@functools.lru_cache(maxsize=None)
def _query(what: str) -> Optional[Any]:
    """`melt -query <what>` as parsed YAML; None without a melt (or if it
    failed)."""
    melt = probe.find_melt()
    if melt is None:
        return None
    try:
        result = subprocess.run(
            [melt, "-query", what], capture_output=True, text=True, env=probe.melt_env(), timeout=60
        )
        return yaml.safe_load(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, yaml.YAMLError):
        return None


def _services(kind: str) -> Optional[set[str]]:
    listing = _query(f"{kind}s")
    if not isinstance(listing, dict):
        return None
    return {str(s) for s in listing.get(f"{kind}s") or []}


def _properties(kind: str, service: str) -> Optional[list[str]]:
    """Its documented properties - None if melt doesn't describe them."""
    meta = _query(f"{kind}={service}")
    if not isinstance(meta, dict) or not meta.get("parameters"):
        return None
    params = [p for p in meta["parameters"] if isinstance(p, dict) and p.get("identifier") is not None]
    names = [str(p["identifier"]) for p in params]
    if service.startswith("frei0r."):
        # frei0r's are numbered; MLT takes each by its name too
        # (frei0r_helper.c), which is the one people know
        names += [str(p["title"]) for p in params if p.get("title")]
    return names


def check(kind: str, service: str, properties: dict[str, Any], ctx: str) -> list[str]:
    """Problems with using `service` (a `kind`: filter or transition)
    so - none if melt knows it, or if there's no melt to ask."""
    known = _services(kind)
    if known is None:
        return []
    if service not in known:
        close = difflib.get_close_matches(service, known, n=3)
        return [msg(f"validation.melt_{kind}_unknown", ctx=ctx, service=service, close=", ".join(close) or "-")]
    documented = _properties(kind, service)
    if documented is None:
        return []
    patterns = [*documented, *_GENERIC]
    problems = []
    for name in properties:
        if not any(fnmatch.fnmatchcase(name, p) for p in patterns):
            close = difflib.get_close_matches(name, documented, n=3)
            problems.append(
                msg("validation.melt_property_unknown", ctx=ctx, service=service, name=name, close=", ".join(close) or "-")
            )
    return problems


def is_known(kind: str, service: str) -> bool:
    """Whether the installed melt has `service` (False without a melt)."""
    known = _services(kind)
    return known is not None and service in known


def _properties_of(raw: Any, ctx: str, errors: list[str]) -> Optional[dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and (v is None or isinstance(v, (str, int, float, bool))) for k, v in raw.items()
    ):
        errors.append(msg("validation.melt_properties_invalid", ctx=ctx, value=raw))
        return None
    return raw


def parse_service(kind: str, service: str, raw_properties: Any, ctx: str, errors: list[str]) -> Optional[MeltService]:
    properties = _properties_of(raw_properties, ctx, errors)
    if properties is None:
        return None
    problems = check(kind, service, properties, ctx)
    if problems:
        errors.extend(problems)
        return None
    return MeltService(service, tuple((k, format_value(v)) for k, v in properties.items() if v is not None))


def parse_filters(raw: Any, ctx: str, errors: list[str]) -> tuple[MeltService, ...]:
    """A clip's `melt:` list: `[{<service>: {<property>: <value>}}, ...]`."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(msg("validation.melt_filters_invalid", ctx=ctx))
        return ()
    filters = []
    for i, item in enumerate(raw):
        item_ctx = f"{ctx}.melt[{i}]"
        if isinstance(item, str):
            item = {item: None}
        if not (isinstance(item, dict) and len(item) == 1 and isinstance(next(iter(item)), str)):
            errors.append(msg("validation.melt_filter_invalid", ctx=item_ctx, item=item))
            continue
        ((service, properties),) = item.items()
        parsed = parse_service("filter", service, properties, item_ctx, errors)
        if parsed is not None:
            filters.append(parsed)
    return tuple(filters)


def to_yaml(service: MeltService) -> dict:
    """The `melt:` entry a filter came from (reverse.py)."""
    return {service.service: dict(service.properties) or None}
