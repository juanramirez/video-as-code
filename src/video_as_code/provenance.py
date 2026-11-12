"""`provenance.yaml` and `video-as-code clean`: which generated files can be
had again, from what, and deleting them once that is checked.

Three mechanisms already say where a file came from; `clean` reads all of
them and adds one more for what they don't cover:

- `sources.lock.yaml` (sources_lock.py): the album items, `url:` downloads
  and the reference video of a project, each with its size and sha256.
- `<file>.input.json` sidecars (derive.py) next to every `.derived/` and
  `.proxies/` file: the input it was made from, by name and sha256. The
  sha256 is looked up in the lock to find the original source.
- `provenance.yaml`, next to the project's YAML: for everything a project's
  own scripts leave behind (`.work/` and anywhere else), which source(s) a
  file or a whole folder is made from and how to make it again:

      files:
        - path: .work/invitados            # a file, or a folder (every file in it)
          sources:                         # ALL must still be there
            - item: "{name}"               # album item named like each file ({name} = its file name)
          recipe: recipes/fetch_album_video.py   # a file that exists in the project...
          command: "python recipes/fetch_album_video.py <name>"   # ...and/or the command line
          note: free text
        - path: .work/enhance
          sources:
            - reference: true              # project.sources.reference
          recipe: recipes/enhance_missing.sh

  A source is exactly one of: `item: NAME` (an item of the project's album,
  or of `album: URL`; `media_id:` pins the item), `url: URL`, `reference:
  true`, `link: BIN_ID` (a `url:` bin clip, from the lock), or `file: PATH`
  (another file of the project; `sha256:` to also check what it holds). A
  `file:` that is gone only counts if it is itself regenerable: covered by
  an entry (or sidecar) whose sources check out, or the reference video.

`clean` deletes a file only when it has a recipe AND every source answers
now (see Net). Anything it can't check - no network, a private video, a
source that isn't recorded - stays, with the reason. A check is a snapshot:
the source may be gone tomorrow (see the warning `clean` prints).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from . import gphotos, links, sources_lock
from .errors import KdenliveAsCodeError
from .i18n import msg

REGISTRY_NAME = "provenance.yaml"
_HEADER = "# What each generated file is made from, and how: see `video-as-code clean --help`.\n"
_PRIMARY = ("item", "url", "reference", "link", "file")
_KEYS = set(_PRIMARY) | {"album", "media_id", "sha256", "size"}
_MAX_DEPTH = 4  # a `file:` source regenerable from another one, and so on


class ProvenanceError(KdenliveAsCodeError):
    pass


# --- the registry ------------------------------------------------------------

def read_registry(base_dir: Path) -> list[dict]:
    """The registry's entries (`files:`); [] if there's none."""
    path = base_dir / REGISTRY_NAME
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProvenanceError(msg("provenance.registry_invalid", path=path, exc=exc)) from exc
    entries = data.get("files") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ProvenanceError(msg("provenance.registry_invalid", path=path, exc="expected a `files:` list"))
    for entry in entries:
        _validate_entry(entry, path)
    return entries


def _validate_entry(entry, path: Path) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].strip():
        raise ProvenanceError(msg("provenance.entry_invalid", path=path, entry=entry, why="needs a `path:`"))
    sources = entry.get("sources", [])
    if not isinstance(sources, list):
        raise ProvenanceError(msg("provenance.entry_invalid", path=path, entry=entry["path"], why="`sources:` is a list"))
    for source in sources:
        problem = _source_problem(source)
        if problem:
            raise ProvenanceError(msg("provenance.entry_invalid", path=path, entry=entry["path"], why=problem))


def _source_problem(source) -> Optional[str]:
    if not isinstance(source, dict):
        return "a source is a map (item:, url:, reference:, link: or file:)"
    primary = [k for k in _PRIMARY if k in source]
    if len(primary) != 1:
        return f"a source has exactly one of {', '.join(_PRIMARY)}: {source}"
    unknown = set(source) - _KEYS
    return f"unknown key(s) {sorted(unknown)} in {source}" if unknown else None


def relative_path(base_dir: Path, path: str | Path) -> str:
    """`path` as the registry spells it: relative to the project's folder,
    '/' separated. Raises ProvenanceError for anything outside it (or the
    folder itself): `clean` never deletes there."""
    full = Path(os.path.normpath(Path(path) if Path(path).is_absolute() else base_dir / path))
    base = Path(os.path.normpath(base_dir))
    if full == base or base not in full.parents:
        raise ProvenanceError(msg("provenance.outside_project", path=path, base=base_dir))
    return full.relative_to(base).as_posix()


def register(
    base_dir: Path,
    path: str | Path,
    sources: list[dict],
    recipe: Optional[str] = None,
    command: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Records in `provenance.yaml` that `path` (file or folder) is made
    from `sources` as the recipe/command says; what the registry already
    says about that path is kept, the new sources added to it (a source
    already there isn't repeated) and the recipe, command and note given
    replacing the old ones. Returns the entry."""
    rel = relative_path(base_dir, path)
    for source in sources:
        problem = _source_problem(source)
        if problem:
            raise ProvenanceError(problem)
    if recipe is not None:
        recipe = relative_path(base_dir, recipe)
    sources = [{**s, "file": relative_path(base_dir, s["file"])} if "file" in s else s for s in sources]
    entries = read_registry(base_dir)
    entry = next((e for e in entries if e["path"] == rel), None)
    if entry is None:
        entry = {"path": rel}
        entries.append(entry)
    merged = list(entry.get("sources", []))
    merged += [s for s in sources if s not in merged]
    entry["sources"] = merged
    for key, value in (("recipe", recipe), ("command", command), ("note", note)):
        if value:
            entry[key] = value
    entries.sort(key=lambda e: e["path"])
    ordered = [{k: e[k] for k in ("path", "sources", "recipe", "command", "note") if k in e} for e in entries]
    (base_dir / REGISTRY_NAME).write_text(
        _HEADER + yaml.safe_dump({"files": ordered}, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return entry


# --- what to check sources against -------------------------------------------

@dataclass
class Verdict:
    ok: bool
    detail: str


class Net:
    """Asks the network whether a source is there. Every method answers a
    Verdict and never raises: a source that can't be reached or told apart
    simply isn't ok. Tests replace it."""

    def __init__(self, album_url: Optional[str], base_dir: Path, report: Callable[[str], None] = lambda t: None):
        from . import fetch  # (it imports a lot: only when really checking)
        self._album_url = album_url
        self._index_cache: dict[str, "fetch._AlbumIndex"] = {}
        self._base_dir = base_dir
        self._report = report
        self._fetch = fetch

    def _index(self, album_url: str):
        if album_url not in self._index_cache:
            self._index_cache[album_url] = self._fetch._AlbumIndex(
                album_url, self._base_dir / self._fetch._NAMES_CACHE, self._report
            )
        return self._index_cache[album_url]

    def album_item(self, album_url: Optional[str], name: str, media_id: Optional[str], size: Optional[int]) -> Verdict:
        album_url = album_url or self._album_url
        if not album_url:
            return Verdict(False, msg("provenance.no_album"))
        try:
            index = self._index(album_url)
            if media_id:
                item = index.by_media_id(media_id)
            else:
                item = index.find(name)
            if item is None:
                return Verdict(False, msg("provenance.item_gone", name=name))
            request = urllib.request.Request(item.download_url, method="HEAD")
            with gphotos._open(request) as response:
                length = response.headers.get("Content-Length")
        except (gphotos.GPhotosError, KdenliveAsCodeError, OSError) as exc:
            return Verdict(False, msg("provenance.check_failed", what=name, exc=exc))
        return _size_verdict(msg("provenance.item_there", name=name), length, size)

    def url(self, url: str, size: Optional[int]) -> Verdict:
        kind = links.kind(url)
        try:
            if kind == "youtube":
                run = subprocess.run(
                    links._yt_dlp_command() + ["--simulate", "--quiet", "--no-warnings", "--no-playlist", url],
                    capture_output=True, text=True, timeout=120,
                )
                if run.returncode != 0:
                    detail = (run.stderr.strip().splitlines() or ["?"])[-1]
                    return Verdict(False, msg("provenance.check_failed", what=url, exc=detail))
                return Verdict(True, msg("provenance.youtube_there", url=url))
            target = url
            if kind == "drive":
                file_id = links.drive_file_id(url)
                if not file_id:
                    return Verdict(False, msg("links.drive_no_id", url=url))
                target = links._DRIVE_DOWNLOAD.format(id=file_id)
            with gphotos._open(target) as response:  # (the body isn't read)
                if "text/html" in (response.headers.get("Content-Type") or ""):
                    return Verdict(False, msg("links.got_a_page", url=url))
                length = response.headers.get("Content-Length")
        except (gphotos.GPhotosError, KdenliveAsCodeError, OSError, subprocess.SubprocessError) as exc:
            return Verdict(False, msg("provenance.check_failed", what=url, exc=exc))
        return _size_verdict(msg("provenance.url_there", url=url), length, size)


def _size_verdict(there: str, length: Optional[str], size: Optional[int]) -> Verdict:
    if size and length and length.isdigit() and int(length) != size:
        return Verdict(False, msg("provenance.size_differs", what=there, now=length, recorded=size))
    return Verdict(True, there)


# --- the candidates -----------------------------------------------------------

@dataclass
class Candidate:
    """A file `clean` may delete: where it is, what it is made from and how."""
    path: Path
    rel: str
    group: str  # what it is printed under: the registry entry, or the sidecar's folder
    sources: list[dict]
    recipe: Optional[str] = None
    command: Optional[str] = None
    extra: list[Path] = field(default_factory=list)  # deleted with it (its sidecar)
    problems: list[str] = field(default_factory=list)  # why it can't be judged at all


@dataclass
class Result:
    candidate: Candidate
    ok: bool
    reasons: list[str]  # why not, or what was verified
    size: int
    removed: bool = False


@dataclass
class Report:
    base_dir: Optional[Path] = None
    results: list[Result] = field(default_factory=list)
    unregistered: dict[str, tuple[int, int]] = field(default_factory=dict)  # `.work/...` folder -> (files, bytes)


def _walk_files(root: Path):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            yield Path(dirpath) / name


def _size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _project_info(yaml_path: Path) -> tuple[Optional[str], list[str], str, str]:
    """(album link, reference links, reference path, sources dir) as the
    project's YAML spells them - read plainly, without checking the rest of
    it: `clean` is for a project whose assets are gone."""
    from . import schema
    from .models import ProjectSpec

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    sources = ((raw.get("project") or {}).get("sources")) or {}
    errors: list[str] = []
    album_url, _ = schema._parse_album(sources.get("album"), errors)
    urls, ref_path = schema._parse_reference(sources.get("reference"), errors)
    sources_dir = sources.get("dir") if isinstance(sources.get("dir"), str) else ProjectSpec.__dataclass_fields__["sources_dir"].default
    return album_url, list(urls or []), ref_path, sources_dir


def _sidecar_candidates(base_dir: Path, sources_dir: str, lock: dict, ref_path: str) -> list[Candidate]:
    items = {e["sha256"]: name for name, e in lock["items"].items() if e.get("sha256")}
    link_ids = {e["sha256"]: cid for cid, e in lock["links"].items() if e.get("sha256")}
    ref_sha = lock["reference"].get("sha256")
    found: list[Candidate] = []
    import json
    for folder in {base_dir / sources_dir / ".derived", base_dir / sources_dir / ".proxies",
                   base_dir / ".derived", base_dir / ".proxies"}:
        if not folder.is_dir():
            continue
        for sidecar in sorted(folder.glob("*.input.json")):
            output = sidecar.with_name(sidecar.name[: -len(".input.json")])
            if not output.is_file():
                continue
            rel = output.relative_to(base_dir).as_posix()
            cand = Candidate(output, rel, folder.relative_to(base_dir).as_posix(), [], extra=[sidecar])
            if folder.name == ".proxies":
                cand.command = "video-as-code build|render|watch --proxy WxH"
            else:
                cand.command = "video-as-code fetch PROJECT.yaml  (derive:)"
            try:
                recorded = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                recorded = {}
            sha, name = recorded.get("sha256"), recorded.get("input")
            if sha in items:
                cand.sources = [{"item": items[sha], "sha256": sha}]
            elif sha in link_ids:
                cand.sources = [{"link": link_ids[sha]}]
            elif sha and sha == ref_sha:
                cand.sources = [{"reference": True}]
            elif name and (folder / name).with_name(name + ".input.json").exists():
                cand.sources = [{"file": (folder / name).relative_to(base_dir).as_posix()}]  # derived from a derived
            else:
                cand.problems.append(msg("provenance.input_unknown", name=name or "?"))
            found.append(cand)
    return found


def _registry_candidates(base_dir: Path, entries: list[dict]) -> dict[str, Candidate]:
    found: dict[str, Candidate] = {}
    # (shallow entries first, so a deeper one overrides it for its own files)
    for entry in sorted(entries, key=lambda e: len(Path(e["path"]).parts)):
        rel = relative_path(base_dir, entry["path"])
        root = base_dir / rel
        if root.is_symlink() or root.is_file():
            files = [root]
        elif root.is_dir():
            files = list(_walk_files(root))
        else:
            files = []
        for path in files:
            file_rel = path.relative_to(base_dir).as_posix()
            found[file_rel] = Candidate(path, file_rel, rel, list(entry.get("sources", [])),
                                        entry.get("recipe"), entry.get("command"))
    return found


# --- judging them --------------------------------------------------------------

class _Judge:
    def __init__(self, base_dir: Path, album_url: Optional[str], ref_urls: list[str], ref_path: str,
                 lock: dict, candidates: dict[str, Candidate], net, entries: list[dict] = ()):
        self.base_dir, self.album_url, self.ref_urls, self.ref_path = base_dir, album_url, ref_urls, ref_path
        self.entries = entries
        self.lock, self.candidates, self.net = lock, candidates, net
        self._cache: dict[str, Verdict] = {}

    def source(self, source: dict, name: str, depth: int = 0) -> Verdict:
        shown = {k: (v.replace("{name}", name) if isinstance(v, str) else v) for k, v in source.items()}
        key = repr(sorted(shown.items(), key=lambda kv: kv[0])) + f"@{depth}"
        if key not in self._cache:
            self._cache[key] = self._source(shown, depth)
        return self._cache[key]

    def _source(self, source: dict, depth: int) -> Verdict:
        if "item" in source:
            name = source["item"]
            entry = self.lock["items"].get(name) or {}
            return self.net.album_item(source.get("album"), name, source.get("media_id") or entry.get("media_id"),
                                       source.get("size") or entry.get("size"))
        if "url" in source:
            return self.net.url(source["url"], source.get("size"))
        if "reference" in source:
            return self._reference()
        if "link" in source:
            entry = self.lock["links"].get(source["link"])
            if not entry or not entry.get("url"):
                return Verdict(False, msg("provenance.link_unlocked", id=source["link"]))
            return self.net.url(entry["url"], None)
        return self._file(source, depth)

    def _reference(self) -> Verdict:
        if not self.ref_urls:
            return Verdict(False, msg("provenance.no_reference"))
        size = self.lock["reference"].get("size")
        verdicts = [self.net.url(url, size) for url in self.ref_urls]
        return next((v for v in verdicts if v.ok), verdicts[0])

    def _file(self, source: dict, depth: int) -> Verdict:
        try:
            rel = relative_path(self.base_dir, source["file"])
        except ProvenanceError as exc:
            return Verdict(False, str(exc))
        path = self.base_dir / rel
        if path.is_file():
            sha = source.get("sha256")
            if sha and gphotos.sha256_of(path) != sha:
                return Verdict(False, msg("provenance.file_changed", path=rel))
            return Verdict(True, msg("provenance.file_present", path=rel))
        if rel == self.ref_path:
            verdict = self._reference()
            return Verdict(verdict.ok, msg("provenance.file_via", path=rel, detail=verdict.detail))
        covering = self.candidates.get(rel) or self._recorded(rel)
        if covering is None or depth >= _MAX_DEPTH:
            return Verdict(False, msg("provenance.file_gone", path=rel))
        reasons = self.judge(covering, depth + 1)
        if reasons:
            return Verdict(False, msg("provenance.file_gone_unchecked", path=rel, why="; ".join(reasons)))
        return Verdict(True, msg("provenance.file_regenerable", path=rel))

    def _recorded(self, rel: str) -> Optional[Candidate]:
        """A file that is gone, as the registry (its deepest entry covering
        it) says it was made. Sidecars go with their file: no record then."""
        best = None
        for entry in self.entries:
            root = relative_path(self.base_dir, entry["path"])
            if (rel == root or rel.startswith(root + "/")) and (best is None or len(root) > len(best[0])):
                best = (root, entry)
        if best is None:
            return None
        root, entry = best
        return Candidate(self.base_dir / rel, rel, root, list(entry.get("sources", [])),
                         entry.get("recipe"), entry.get("command"))

    def judge(self, cand: Candidate, depth: int = 0) -> list[str]:
        """Why `cand` can't be deleted; [] if it can."""
        reasons = list(cand.problems)
        if cand.recipe:
            try:
                if not (self.base_dir / relative_path(self.base_dir, cand.recipe)).is_file():
                    reasons.append(msg("provenance.recipe_missing", recipe=cand.recipe))
            except ProvenanceError as exc:
                reasons.append(str(exc))
        elif not cand.command:
            reasons.append(msg("provenance.no_recipe"))
        if not cand.sources and not cand.problems:
            reasons.append(msg("provenance.no_sources"))
        for source in cand.sources:
            verdict = self.source(source, cand.path.name, depth)
            if not verdict.ok:
                reasons.append(verdict.detail)
        return reasons


def verified_note(judge: _Judge, cand: Candidate) -> list[str]:
    return [judge.source(s, cand.path.name).detail for s in cand.sources]


# --- clean -----------------------------------------------------------------------

def clean(
    yaml_path: Path,
    only: Optional[list[str]] = None,
    dry_run: bool = False,
    net=None,
    report_progress: Callable[[str], None] = lambda text: None,
) -> Report:
    """Deletes what is regenerable AND whose sources answer now (see the
    module docstring); `only` restricts it to those files or folders
    (relative to the project's folder). With `dry_run`, deletes nothing."""
    base_dir = yaml_path.resolve().parent
    album_url, ref_urls, ref_path, sources_dir = _project_info(yaml_path)
    lock = {
        "items": sources_lock.read(base_dir),
        "links": sources_lock.read_links(base_dir),
        "reference": sources_lock.read_reference(base_dir),
    }
    entries = read_registry(base_dir)
    candidates = _registry_candidates(base_dir, entries)
    for cand in _sidecar_candidates(base_dir, sources_dir, lock, ref_path):
        candidates.setdefault(cand.rel, cand)
    if net is None:
        net = Net(album_url, base_dir, report_progress)
    judge = _Judge(base_dir, album_url, ref_urls, ref_path, lock, candidates, net, entries)

    selected = list(candidates.values())
    if only:
        prefixes = [relative_path(base_dir, p) for p in only]
        selected = [c for c in selected if any(c.rel == p or c.rel.startswith(p + "/") for p in prefixes)]
    report = Report(base_dir)
    for cand in sorted(selected, key=lambda c: c.rel):
        reasons = judge.judge(cand)
        report.results.append(Result(cand, not reasons, reasons or verified_note(judge, cand), _size(cand.path)))
    work = base_dir / ".work"
    if work.is_dir():
        for result_path in _walk_files(work):
            rel = result_path.relative_to(base_dir).as_posix()
            if rel not in candidates:
                parts = Path(rel).parts
                top = "/".join(parts[:2]) if len(parts) > 2 else ".work/(loose files)"
                files, size = report.unregistered.get(top, (0, 0))
                report.unregistered[top] = (files + 1, size + _size(result_path))
    if not dry_run:
        apply(report)
    return report


def apply(report: Report) -> None:
    """Deletes what `report` found ok (what `clean` does without `dry_run`;
    the CLI calls it after the person has confirmed)."""
    for result in report.results:
        if result.ok and not result.removed:
            result.removed = _remove(result.candidate, report.base_dir)


def _remove(cand: Candidate, base_dir: Path) -> bool:
    try:
        for path in [cand.path, *cand.extra]:
            path.unlink(missing_ok=True)  # (a symlink goes, never what it points to)
        root = base_dir / cand.group  # (folders it emptied go too, but never the entry's own)
        parent = cand.path.parent
        while parent != root and root in parent.parents:
            try:
                parent.rmdir()  # only if now empty
            except OSError:
                break
            parent = parent.parent
    except OSError:
        return False
    return True
