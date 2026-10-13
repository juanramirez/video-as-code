"""`video-as-code fetch`: downloads the bin clips that come from the
project's shared album (BinClipSpec.source) and aren't on disk yet.

Alongside the YAML it keeps `sources.lock.yaml` (see sources_lock.py):
for each original file name, the album item it was found as, its size,
sha256 and capture time. That's what makes later fetches cheap (no
search: the item is known), catches a local file that doesn't match what
was downloaded, and names local files `<capture date>_<bin id><ext>`.
Nothing in it is meant to be read or edited by hand - the YAML only ever
names items by their original file name.

Finding an item by file name means asking Google for each candidate's
name (one HEAD request each - the album listing doesn't include names),
so candidates are tried closest-capture-time-first (see
gphotos.rank_by_name_time) a few at a time, and every name learned is
cached in `.fetch_cache/names.json` so it's never asked twice.

`derive:` clips (derive.py) take part too: an album item one of them is
derived from is downloaded like any `source:` clip (even if nothing else
uses it), and once downloads are done every derived clip whose input is
on disk is generated, so a fetched project builds without further work.
"""
from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import derive, gphotos, sources_lock
from .errors import KdenliveAsCodeError
from .i18n import msg
from .models import ProjectDoc
from .schema import load_yaml

LOCK_NAME = sources_lock.LOCK_NAME
_NAMES_CACHE = Path(".fetch_cache") / "names.json"
# HEAD requests in flight at once while searching for a file name.
_LOOKUP_BATCH = 8


class FetchError(KdenliveAsCodeError):
    pass


@dataclass
class FetchResult:
    downloaded: list[str] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)  # bin ids of the derive: clips (re)generated
    renamed: list[str] = field(default_factory=list)  # already on disk under another local name: renamed


def _read_names_cache(path: Path) -> dict[str, str]:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


class _AlbumIndex:
    """The album's items, listed once on first use, plus what's known of
    their original file names."""

    def __init__(self, album_url: str, names_cache_path: Path, report: Callable[[str], None]):
        self._album_url = album_url
        self._names_cache_path = names_cache_path
        self._names = _read_names_cache(names_cache_path)  # media id -> original file name
        self._report = report
        self._items: Optional[list[gphotos.AlbumItem]] = None

    @property
    def items(self) -> list[gphotos.AlbumItem]:
        if self._items is None:
            self._report(msg("fetch.listing_album"))
            self._items = gphotos.list_album(self._album_url)
            self._report(msg("fetch.album_listed", n=len(self._items)))
        return self._items

    def by_media_id(self, media_id: str) -> Optional[gphotos.AlbumItem]:
        return next((i for i in self.items if i.media_id == media_id), None)

    def find(self, name: str) -> Optional[gphotos.AlbumItem]:
        candidates = gphotos.rank_by_name_time(self.items, name)
        for item in candidates:  # names already known cost nothing
            if self._names.get(item.media_id) == name:
                return item
        unknown = [i for i in candidates if i.media_id not in self._names]
        with ThreadPoolExecutor(max_workers=_LOOKUP_BATCH) as pool:
            for start in range(0, len(unknown), _LOOKUP_BATCH):
                batch = unknown[start:start + _LOOKUP_BATCH]
                for item, item_name in zip(batch, pool.map(gphotos.original_filename, batch)):
                    self._names[item.media_id] = item_name
                self._save_names()
                match = next((i for i in batch if self._names[i.media_id] == name), None)
                if match is not None:
                    return match
        return None

    def _save_names(self) -> None:
        self._names_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._names_cache_path.write_text(json.dumps(self._names, indent=0, sort_keys=True), encoding="utf-8")


def fetch(
    yaml_path: Path,
    on_progress: Optional[Callable[[str], None]] = None,
    verify: bool = False,
) -> FetchResult:
    """Downloads every `source:` bin clip of `yaml_path` that isn't on disk
    (or, with `verify`, whose sha256 doesn't match the lock) and updates
    the lock, then generates the derive: clips. Raises FetchError listing
    the names the album doesn't have and any derive: clip that failed."""
    report = on_progress or (lambda text: None)
    doc = load_yaml(yaml_path)
    base_dir = doc.source_path.parent
    sources_root = base_dir / doc.project.sources_dir
    # (original name, bin id, local_name override) per file to have on
    # disk; a derive: input has no bin id and keeps its original name
    jobs: list[tuple[str, Optional[str], Optional[str]]] = [
        (clip.source, clip.id, clip.local_name) for clip in doc.bin if clip.source is not None
    ]
    for clip in doc.bin:
        if clip.derive is not None and clip.derive.source is not None:
            job = (clip.derive.source, None, None)
            if job not in jobs:
                jobs.append(job)
    result = FetchResult()
    missing: list[str] = []
    if jobs:
        missing = _download(doc, base_dir, sources_root, jobs, result, report, verify)

    failures = [msg("fetch.not_in_album", names=", ".join(missing))] if missing else []
    if any(clip.derive is not None for clip in doc.bin):
        derive_errors: list[str] = []
        result.derived = derive.ensure_all(
            doc.bin, sources_root, derive_errors, on_generate=lambda clip_id: report(msg("fetch.deriving", id=clip_id))
        )
        # a derive: clip whose album input is missing was already reported above
        not_downloaded = {
            msg("validation.derive_input_not_fetched", id=clip.id, path=sources_root / clip.derive.source)
            for clip in doc.bin
            if clip.derive is not None and clip.derive.source in missing
        }
        failures += [e for e in derive_errors if e not in not_downloaded]
    if failures:
        raise FetchError("\n".join(failures))
    return result


def _download(
    doc: ProjectDoc,
    base_dir: Path,
    sources_root: Path,
    jobs: list[tuple[str, Optional[str], Optional[str]]],
    result: FetchResult,
    report: Callable[[str], None],
    verify: bool,
) -> list[str]:
    """The download half of `fetch`; returns the names the album doesn't have.

    A file's local name depends on its capture date (sources_lock.local_name),
    which only the album knows - so it's settled per job once its lock
    entry has a `taken`, filling that in from the album first when the
    lock predates it. A file that's already on disk under another name
    (its bin id changed, or it was fetched before its date was known) is
    renamed - or copied, if that other name is still wanted too - rather
    than downloaded again."""
    entries = sources_lock.read(base_dir)
    index = _AlbumIndex(doc.project.album_url, base_dir / _NAMES_CACHE, report)

    def dest_of(name: str, clip_id: Optional[str], override: Optional[str]) -> Path:
        if clip_id is None:
            return sources_root / name
        return sources_root / sources_lock.local_name(clip_id, name, entries.get(name), override)

    # backfill capture dates the lock doesn't have yet (one album listing)
    for name, clip_id, override in jobs:
        entry = entries.get(name) or {}
        if clip_id is not None and not override and entry.get("media_id") and not entry.get("taken"):
            item = index.by_media_id(entry["media_id"])
            if item is not None:
                entries[name] = {**entry, "taken": sources_lock.taken_local(item.taken_ms, item.tz_offset_ms)}
    missing: list[str] = []
    for n, (name, clip_id, override) in enumerate(jobs, start=1):
        entry = entries.get(name) or {}
        dest = dest_of(name, clip_id, override)
        if dest.exists() and _matches(dest, entry, verify):
            if not entry.get("sha256"):  # on disk before the lock knew it: record it
                entries[name] = {**entry, "size": dest.stat().st_size, "sha256": gphotos.sha256_of(dest)}
            result.up_to_date.append(name)
            continue
        existing = _local_copy(sources_root, name, entry, dest)
        if existing is not None:
            # (recomputed: a download earlier in this run may have just
            # settled another job's date, and with it its local name)
            if existing in {dest_of(*job) for job in jobs}:
                shutil.copy2(existing, dest)
            else:
                existing.replace(dest)
            report(msg("fetch.renamed", old=existing.name, new=dest.name))
            result.renamed.append(name)
            continue
        item = index.by_media_id(entry["media_id"]) if entry.get("media_id") else None
        if item is None:
            item = index.find(name)
        if item is None:
            missing.append(name)
            continue
        taken = sources_lock.taken_local(item.taken_ms, item.tz_offset_ms)
        entries[name] = {**entry, "taken": taken}
        dest = dest_of(name, clip_id, override)  # now that the date is known
        report(msg("fetch.downloading", n=n, total=len(jobs), name=dest.name))
        size, sha256 = gphotos.download(item, dest)
        if entry.get("sha256") and entry["sha256"] != sha256:
            report(msg("fetch.changed_in_album", name=name))
        entries[name] = {"media_id": item.media_id, "size": size, "sha256": sha256, "taken": taken}
        result.downloaded.append(name)
        sources_lock.write(base_dir, entries)  # after each one: an interrupted fetch keeps its progress

    sources_lock.write(base_dir, entries)
    return missing


def _local_copy(sources_root: Path, name: str, entry: dict, dest: Path) -> Optional[Path]:
    """A file already in `sources_root` with exactly the content the lock
    records for `name` (same size and sha256), other than `dest`: under
    its original name, or under any `<capture date>_*` name with the same
    extension (an earlier local name). None if there's none or the lock
    doesn't know the content yet."""
    if not entry.get("sha256") or not sources_root.is_dir():
        return None
    candidates = [sources_root / name]
    if entry.get("taken"):
        date = str(entry["taken"])[:10].replace("-", "")
        candidates += sorted(sources_root.glob(f"{date}_*{Path(name).suffix.lower()}"))
    for path in candidates:
        if path == dest or not path.is_file() or path.stat().st_size != entry.get("size"):
            continue
        if gphotos.sha256_of(path) == entry["sha256"]:
            return path
    return None


def _matches(path: Path, entry: dict, verify: bool) -> bool:
    if entry.get("size") is not None and path.stat().st_size != entry["size"]:
        return False
    if verify and entry.get("sha256"):
        return gphotos.sha256_of(path) == entry["sha256"]
    return True
