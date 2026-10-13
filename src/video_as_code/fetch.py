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

`url:` clips (links.py) are downloaded too, each from the first of its
links that works, and recorded in the lock's `links:` section; one that's
on disk, matches the lock and came from one of its current links isn't
downloaded again.

What can't be fetched doesn't stop the rest: each failure (an album
name that isn't there, a download that broke, a clip none of whose links
work) becomes a Problem, and FetchError carries them all at the end.

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

from . import derive, gphotos, links, sources_lock
from .errors import KdenliveAsCodeError
from .i18n import msg
from .models import BinClipSpec, ProjectDoc
from .schema import load_yaml

LOCK_NAME = sources_lock.LOCK_NAME
_NAMES_CACHE = Path(".fetch_cache") / "names.json"
# HEAD requests in flight at once while searching for a file name.
_LOOKUP_BATCH = 8


@dataclass
class Problem:
    """Something `fetch` couldn't get. Exactly one of `source` (an album
    item, by original file name) and `link_clip` (the id of a `url:` clip)
    is set. `clip_ids` are the bin clips that use it - for an album item,
    `derive:` clips taking it as their input are among them, flagged in
    `derive_ids` (they can't swap it for a link: derive: only reads the
    album, a local path or another clip)."""

    reason: str
    source: Optional[str] = None
    link_clip: Optional[str] = None
    clip_ids: list[str] = field(default_factory=list)
    derive_ids: list[str] = field(default_factory=list)


class FetchError(KdenliveAsCodeError):
    def __init__(self, text: str, problems: Optional[list[Problem]] = None):
        super().__init__(text)
        # the fixable part of `text`; whatever else it says (a derive: that
        # failed for some other reason) has no Problem
        self.problems = problems or []


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
        # a listing that failed fails every later job at once, rather than
        # each one waiting for its own timeout
        self._listing_error: Optional[gphotos.GPhotosError] = None

    @property
    def items(self) -> list[gphotos.AlbumItem]:
        if self._listing_error is not None:
            raise self._listing_error
        if self._items is None:
            self._report(msg("fetch.listing_album"))
            try:
                self._items = gphotos.list_album(self._album_url)
            except gphotos.GPhotosError as exc:
                self._listing_error = exc
                raise
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
    the lock, then generates the derive: clips. Raises FetchError (with its
    `problems`) listing whatever couldn't be had - after doing everything
    else."""
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
    problems: list[Problem] = []
    failed: dict[str, str] = {}
    if jobs:
        failed = _download(doc, base_dir, sources_root, jobs, result, report, verify)
        for name, reason in failed.items():
            problems.append(
                Problem(
                    reason=reason,
                    source=name,
                    clip_ids=[c.id for c in doc.bin if c.source == name or (c.derive and c.derive.source == name)],
                    derive_ids=[c.id for c in doc.bin if c.derive and c.derive.source == name],
                )
            )
    link_clips = [clip for clip in doc.bin if clip.url is not None]
    if link_clips:
        for clip_id, reason in _download_links(base_dir, sources_root, link_clips, result, report, verify).items():
            problems.append(Problem(reason=reason, link_clip=clip_id, clip_ids=[clip_id]))
    failures = [p.reason for p in problems]
    if any(clip.derive is not None for clip in doc.bin):
        derive_errors: list[str] = []
        result.derived = derive.ensure_all(
            doc.bin, sources_root, derive_errors, on_generate=lambda clip_id: report(msg("fetch.deriving", id=clip_id))
        )
        # a derive: clip whose album input is missing was already reported above
        not_downloaded = {
            msg("validation.derive_input_not_fetched", id=clip.id, path=sources_root / clip.derive.source)
            for clip in doc.bin
            if clip.derive is not None and clip.derive.source in failed
        }
        failures += [e for e in derive_errors if e not in not_downloaded]
    if failures:
        raise FetchError("\n".join(failures), problems)
    return result


def _download(
    doc: ProjectDoc,
    base_dir: Path,
    sources_root: Path,
    jobs: list[tuple[str, Optional[str], Optional[str]]],
    result: FetchResult,
    report: Callable[[str], None],
    verify: bool,
) -> dict[str, str]:
    """The download half of `fetch`; returns why each name that couldn't
    be had failed (not in the album, or the album/download broke).

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
            try:
                item = index.by_media_id(entry["media_id"])
            except gphotos.GPhotosError:
                break  # no album: the dates stay unknown; the jobs below report it
            if item is not None:
                entries[name] = {**entry, "taken": sources_lock.taken_local(item.taken_ms, item.tz_offset_ms)}
    failed: dict[str, str] = {}
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
        if name in failed:
            continue  # another clip with the same source already failed it
        try:
            item = index.by_media_id(entry["media_id"]) if entry.get("media_id") else None
            if item is None:
                item = index.find(name)
            if item is None:
                failed[name] = msg("fetch.not_in_album", name=name)
                continue
            taken = sources_lock.taken_local(item.taken_ms, item.tz_offset_ms)
            entries[name] = {**entry, "taken": taken}
            dest = dest_of(name, clip_id, override)  # now that the date is known
            report(msg("fetch.downloading", n=n, total=len(jobs), name=dest.name))
            size, sha256 = gphotos.download(item, dest)
        except gphotos.GPhotosError as exc:
            failed[name] = msg("fetch.download_failed", name=name, error=exc)
            continue
        if entry.get("sha256") and entry["sha256"] != sha256:
            report(msg("fetch.changed_in_album", name=name))
        entries[name] = {"media_id": item.media_id, "size": size, "sha256": sha256, "taken": taken}
        result.downloaded.append(name)
        sources_lock.write(base_dir, entries)  # after each one: an interrupted fetch keeps its progress

    sources_lock.write(base_dir, entries)
    return failed


def _download_links(
    base_dir: Path,
    sources_root: Path,
    clips: list[BinClipSpec],
    result: FetchResult,
    report: Callable[[str], None],
    verify: bool,
) -> dict[str, str]:
    """The `url:` half of `fetch`; returns why each clip none of whose
    links could be downloaded failed, by bin id. Editing a clip's `url:`
    so that the link it was downloaded from is no longer listed downloads
    it again."""
    entries = sources_lock.read_links(base_dir)
    failures: dict[str, str] = {}
    for clip in clips:
        entry = entries.get(clip.id) or {}
        if entry.get("url") in clip.url and clip.path.is_file() and _matches(clip.path, entry, verify):
            result.up_to_date.append(clip.id)
            continue
        errors: list[str] = []
        got: Optional[Path] = None
        for url in clip.url:
            report(msg("fetch.link_downloading", id=clip.id, url=url))
            try:
                got = links.download(url, sources_root, clip.id)
                break
            except links.LinkError as exc:
                errors.append(str(exc))
                if url != clip.url[-1]:
                    report(msg("fetch.link_failed", id=clip.id, error=exc))
        if got is None:
            failures[clip.id] = msg("fetch.links_failed", id=clip.id, errors="\n  ".join(errors))
            continue
        if clip.local_name:
            if Path(clip.local_name).suffix.lower() != got.suffix:
                got.unlink()
                failures[clip.id] = msg("fetch.link_local_name_extension", id=clip.id, name=clip.local_name, ext=got.suffix)
                continue
            got = got.replace(sources_root / clip.local_name)
        elif clip.path != got and clip.path.is_file():
            clip.path.unlink()  # an earlier download with another extension (e.g. from the fallback link)
        sha256 = gphotos.sha256_of(got)
        if entry.get("sha256") and entry["sha256"] != sha256:
            report(msg("fetch.link_changed", id=clip.id))
        entries[clip.id] = {"url": url, "ext": got.suffix, "size": got.stat().st_size, "sha256": sha256}
        sources_lock.write(base_dir, links=entries)
        result.downloaded.append(clip.id)
    return failures


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
