"""Automatic pruning before a render: files a project's renders haven't
needed for `project.sources.prune_after_days` days (default 7) are deleted
- but only files the tool can always bring back by itself:

- album items used only as `derive:` inputs, once every output made from
  them is current and records the lock's sha256 for them (what `fetch
  --prune` deletes at once - see derive.is_current_by_record), and album
  items under their original name that nothing uses any more. `fetch`
  downloads them again if they're ever needed;
- generated files: outputs in <sources dir>/.derived/ (derive.py) and
  light copies in .proxies/ (proxies.py) that no render has used lately -
  stale generations, or ones a spec change left behind. They're made
  again when needed.

Nothing else is ever touched: a file only on this machine (one the lock
doesn't know) stays, however old.

"Used" is recorded per file, by date, in .fetch_cache/last_used.json:
`build`, `validate` and `render` record every file the resolved project
reads (originals too when previewing with --proxy). A derive: input also
counts as used whenever an output is generated from it (that output's
.input.json sidecar is written then). A file seen for the first time is
recorded, not deleted - so turning this on never deletes anything on the
spot. Several YAML files sharing a sources folder (a project and its
preview) share the record too, so a file only goes once none of them has
used it for that long.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable, Optional

from . import derive, proxies, sources_lock
from .models import ProjectDoc, ResolvedProject

DEFAULT_DAYS = 7
_USAGE_FILE = Path(".fetch_cache") / "last_used.json"
_DAY_S = 24 * 3600


def _usage_path(project_dir: Path) -> Path:
    return project_dir / _USAGE_FILE


def _read_usage(project_dir: Path) -> dict[str, float]:
    try:
        data = json.loads(_usage_path(project_dir).read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _key(path: Path, project_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def used_paths(project: ResolvedProject) -> set[Path]:
    """Every file the resolved project reads: its bin clips and whatever
    its placements point at (a boomerang's pre-reversed copy, a proxy)."""
    paths = {clip.path for clip in project.bin_clips}
    paths |= {p.bin_clip.path for track in project.tracks for p in track.placements}
    for clip in project.bin_clips:  # (a part's, in a project of several files - parts.py)
        if clip.sequence is not None:
            paths |= used_paths(clip.sequence)
    return paths


def record_use(project_dir: Path, paths: Iterable[Path], now: Optional[float] = None) -> None:
    """Marks `paths` as used `now` (best effort: a read-only folder just
    means nothing gets pruned)."""
    now = time.time() if now is None else now
    usage = _read_usage(project_dir)
    for path in paths:
        usage[_key(path, project_dir)] = now
    target = _usage_path(project_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(usage, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def prune(
    doc: ProjectDoc,
    needed: set[Path],
    days: float,
    now: Optional[float] = None,
) -> tuple[list[Path], int]:
    """Deletes what the module docstring describes; `needed` are the files
    the render about to start reads (never deleted, whatever their age).
    Returns (deleted files, bytes freed)."""
    if days <= 0:
        return [], 0
    now = time.time() if now is None else now
    project_dir = doc.source_path.parent
    sources_root = project_dir / doc.project.sources_dir
    usage = _read_usage(project_dir)
    needed_keys = {_key(p, project_dir) for p in needed}
    cutoff = now - days * _DAY_S

    def last_used(path: Path) -> Optional[float]:
        return usage.get(_key(path, project_dir))

    def old(path: Path, also: Iterable[float] = ()) -> bool:
        times = [t for t in [last_used(path), *also] if t is not None]
        # never seen before: start counting now rather than delete it
        return bool(times) and max(times) < cutoff

    candidates: list[Path] = []
    lock = sources_lock.read(project_dir)

    # album items under their original name (derive-only inputs, or no
    # longer used at all) - `fetch` can always download them again
    plain_sources = {c.source for c in doc.bin if c.source is not None}
    for name, entry in lock.items():
        path = sources_root / sources_lock.source_file_name(name)
        if name in plain_sources or not path.is_file() or not entry.get("sha256"):
            continue
        users = [c for c in doc.bin if c.derive is not None and c.derive.source == name]
        # every output made from it must stand on its own without it
        if not all(derive.is_current_by_record(c.path, entry["sha256"]) for c in users):
            continue
        generated = [derive._sidecar(c.path).stat().st_mtime for c in users if derive._sidecar(c.path).exists()]
        if old(path, generated):
            candidates.append(path)

    # generated files: derive outputs and light copies (with their sidecars)
    for folder in (sources_root / derive.DERIVED_DIR, project_dir / proxies.PROXY_DIR):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.name.endswith(".input.json") or not path.is_file():
                continue
            if old(path, [derive._sidecar(path).stat().st_mtime] if derive._sidecar(path).exists() else []):
                candidates.append(path)

    deleted: list[Path] = []
    freed = 0
    for path in candidates:
        if _key(path, project_dir) in needed_keys:
            continue
        sidecar = derive._sidecar(path)
        for victim in (path, sidecar):
            if victim.is_file():
                freed += victim.stat().st_size
                victim.unlink()
        deleted.append(path)
        usage.pop(_key(path, project_dir), None)
    if deleted:
        try:
            _usage_path(project_dir).write_text(json.dumps(usage, indent=0, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    return deleted, freed
