"""`presets:` and `use:`: named sets of keys, written once and used
anywhere a project file has a map - a scene, a clip, a bin entry, a
transition:

    presets:
      page: {transition: gl, gl: BookFlip, transition_duration: 0.8}
      warm: {lut: looks/warm.cube, film_grain: {noise: 12}}
    scenes:
      - use: page
        clips: [a.jpg, {path: b.jpg, use: warm}]

`use:` takes a name or a list of them; the presets' keys go in first, in
that order, and the keys written next to `use:` last - so they win, a
whole value each (a map isn't merged into the preset's). A preset may
`use:` others. `presets_file:` names a YAML file (relative to the
project file) whose own `presets:` come first: a project's presets of
the same name replace them.

Expanded before anything else reads the file (schema.load_yaml), so
`use:` works with every key there is or will be. Relative paths in a
preset are relative to the project file that uses it, like the rest of
that file's - a presets file shared by projects in other folders takes
absolute ones.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .i18n import msg


def _names(raw: Any) -> list | None:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and raw and all(isinstance(n, str) for n in raw):
        return raw
    return None


def _from_file(path: Path, errors: list[str]) -> dict:
    if not path.is_file():
        errors.append(msg("validation.presets_file_missing", path=path))
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(msg("validation.yaml_invalid", path=path, exc=exc))
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("presets"), dict) or set(data) - {"presets"}:
        errors.append(msg("validation.presets_file_invalid", path=path))
        return {}
    return data["presets"]


def expand(data: dict, project_file: Path, errors: list[str]) -> dict:
    """`data` (a project file as read) with every `use:` replaced by its
    presets' keys, and `presets`/`presets_file` gone."""
    return expand_with(data, project_file, errors)[0]


def expand_with(data: dict, project_file: Path, errors: list[str], inherited: dict | None = None) -> tuple[dict, dict]:
    """expand(), starting from `inherited` presets (a part's, from the
    file that includes it - parts.py); also returns the presets in force
    in this file, for the parts it includes."""
    data = dict(data)
    presets: dict = dict(inherited or {})
    origin: dict[str, str] = {name: "an including file" for name in presets}  # (where each came from, for messages)
    file_raw = data.pop("presets_file", None)
    if file_raw is not None:
        if not isinstance(file_raw, str):
            errors.append(msg("validation.presets_file_invalid", path=file_raw))
        else:
            path = Path(file_raw) if Path(file_raw).is_absolute() else project_file.parent / file_raw
            for name, keys in _from_file(path, errors).items():
                presets[name], origin[name] = keys, str(path.name)
    local = data.pop("presets", None)
    if local is not None and not isinstance(local, dict):
        errors.append(msg("validation.presets_not_map"))
        local = {}
    for name, keys in (local or {}).items():
        presets[name], origin[name] = keys, project_file.name
    for name, keys in presets.items():
        if not isinstance(keys, dict):
            errors.append(msg("validation.preset_not_map", name=name, where=origin[name], value=keys))
    presets = {name: keys for name, keys in presets.items() if isinstance(keys, dict)}

    resolved: dict[str, dict] = {}

    def preset(name: str, chain: tuple[str, ...], where: str) -> dict | None:
        """A preset's keys with its own `use:` expanded."""
        if name in chain:
            errors.append(msg("validation.preset_cycle", chain=" -> ".join((*chain, name))))
            return None
        if name not in presets:
            errors.append(msg("validation.preset_unknown", where=where, name=name, known=", ".join(sorted(presets)) or "-"))
            return None
        if name not in resolved:
            expanded = merge(presets[name], (*chain, name), f"presets.{name}")
            if expanded is None:
                return None
            resolved[name] = expanded
        return resolved[name]

    def merge(node: dict, chain: tuple[str, ...], where: str) -> dict | None:
        if "use" not in node:
            return node
        names = _names(node["use"])
        if names is None:
            errors.append(msg("validation.use_invalid", where=where, value=node["use"]))
            return None
        out: dict = {}
        for name in names:
            keys = preset(name, chain, where)
            if keys is None:
                return None
            out.update(copy.deepcopy(keys))
        out.update({k: v for k, v in node.items() if k != "use"})
        return out

    def walk(node: Any, where: str) -> Any:
        if isinstance(node, dict):
            merged = merge(node, (), where)
            node = merged if merged is not None else {k: v for k, v in node.items() if k != "use"}
            return {k: walk(v, f"{where}.{k}" if where else str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{where}[{i}]") for i, v in enumerate(node)]
        return node

    return walk(data, ""), presets
