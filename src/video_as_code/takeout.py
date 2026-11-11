"""Read supported media entries from Google Takeout ZIPs on demand.

Only one bounded set of candidate files is materialized at a time. ZIP
metadata and unsupported members (including Takeout JSON sidecars) stay in
the archives; callers can later copy only matched candidates if needed.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

from .errors import KdenliveAsCodeError
from .i18n import msg


class TakeoutError(KdenliveAsCodeError):
    pass


@dataclass(frozen=True)
class TakeoutAsset:
    key: str
    display_name: str
    archive: Path
    member: str
    info: zipfile.ZipInfo
    size: int
    kind: str
    taken_date: dt.date | None
    output_relative: Path

    @contextlib.contextmanager
    def open_stream(self):
        """Read decompressed bytes directly from the ZIP, without a local copy."""
        try:
            with zipfile.ZipFile(self.archive) as archive:
                with archive.open(self.info) as stream:
                    yield stream
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise TakeoutError(msg("takeout.bad_zip", path=self.archive, error=exc)) from exc

    @contextlib.contextmanager
    def materialize(self, directory: Path) -> Iterator[Path]:
        """Extract this one member into a private scratch folder, then remove it."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="asset-", dir=directory) as temporary:
            path = Path(temporary) / PurePosixPath(self.member).name
            try:
                with zipfile.ZipFile(self.archive) as archive, archive.open(self.info) as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise TakeoutError(msg("takeout.bad_zip", path=self.archive, error=exc)) from exc
            yield path

    def copy_to(self, root: Path) -> Path:
        """Persist this matched asset under `root`, preserving Takeout paths."""
        destination = root / self.output_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(self.archive) as archive, archive.open(self.info) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise TakeoutError(msg("takeout.bad_zip", path=self.archive, error=exc)) from exc
        return destination


@dataclass(frozen=True)
class LocalTakeoutAsset:
    """A retained candidate from an earlier incremental Takeout pass."""
    key: str
    display_name: str
    local_path: Path
    size: int
    kind: str
    taken_date: dt.date | None

    @property
    def name(self) -> str:
        return PurePosixPath(self.display_name.rsplit(":", 1)[-1].replace("\\", "/")).name

    @contextlib.contextmanager
    def materialize(self, directory: Path) -> Iterator[Path]:
        yield self.local_path

    @contextlib.contextmanager
    def open_stream(self):
        with self.local_path.open("rb") as stream:
            yield stream

    def copy_to(self, root: Path) -> Path:
        destination = root / self.local_path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.local_path.resolve() != destination.resolve():
            shutil.copy2(self.local_path, destination)
        return destination


_FILENAME_DATE = re.compile(r"(?<!\d)(\d{4})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)")


def _member_date(
    info: zipfile.ZipInfo,
    archive: zipfile.ZipFile,
    sidecars: dict[tuple[int, str], zipfile.ZipInfo],
) -> dt.date | None:
    """Read Google's capture date from the paired JSON sidecar, if present."""
    relative = PurePosixPath(info.filename.replace("\\", "/"))
    parent = relative.parent
    name = relative.name
    stem = PurePosixPath(name).stem
    possibilities = [
        parent / f"{name}.json",
        parent / f"{name}.supplemental-metadata.json",
        parent / f"{stem}.json",
        parent / f"{stem}.supplemental-metadata.json",
    ]
    for possible in possibilities:
        sidecar = sidecars.get((id(archive), possible.as_posix()))
        if sidecar is not None:
            try:
                with archive.open(sidecar) as stream:
                    data = json.load(stream)
                timestamp = data.get("photoTakenTime", {}).get("timestamp")
                if timestamp is not None:
                    return dt.datetime.fromtimestamp(int(timestamp), tz=dt.timezone.utc).date()
            except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, zipfile.BadZipFile):
                continue
    match = _FILENAME_DATE.search(relative.name)
    if match:
        try:
            return dt.date(*(int(part) for part in match.groups()))
        except ValueError:
            pass
    return None


def scan_zip_folder(
    folder: Path,
    kind_for_path: Callable[[Path], str],
    *,
    archive_paths: list[Path] | None = None,
    prefer_from: dt.date | None = None,
    prefer_to: dt.date | None = None,
) -> list[TakeoutAsset]:
    """Index supported media entries in every ZIP under `folder` without
    extracting them. Unsafe archive paths and non-media members are skipped."""
    archives = archive_paths if archive_paths is not None else sorted(
        path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() == ".zip"
    )
    if not archives:
        raise TakeoutError(msg("takeout.no_zip_files", path=folder))

    assets: list[TakeoutAsset] = []
    with contextlib.ExitStack() as stack:
        opened: list[tuple[Path, zipfile.ZipFile, list[zipfile.ZipInfo]]] = []
        for archive_path in archives:
            try:
                archive = stack.enter_context(zipfile.ZipFile(archive_path))
                opened.append((archive_path, archive, archive.infolist()))
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise TakeoutError(msg("takeout.bad_zip", path=archive_path, error=exc)) from exc

        sidecars: dict[tuple[int, str], zipfile.ZipInfo] = {}
        if prefer_from is not None or prefer_to is not None:
            for _path, archive, infos in opened:
                for info in infos:
                    if info.filename.lower().endswith(".json"):
                        sidecars[(id(archive), info.filename.replace("\\", "/"))] = info

        for archive_index, (archive_path, archive, infos) in enumerate(opened, 1):
            try:
                used_outputs: set[Path] = set()
                for member_index, info in enumerate(infos, 1):
                    if info.is_dir() or info.file_size == 0:
                        continue
                    normalized = info.filename.replace("\\", "/")
                    relative = PurePosixPath(normalized)
                    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                        continue
                    kind = kind_for_path(Path(relative.name))
                    if kind not in ("image", "video"):
                        continue
                    taken = _member_date(info, archive, sidecars)
                    archive_prefix = f"{archive_index:04d}_{archive_path.stem}"
                    output_relative = Path(archive_prefix, *relative.parts)
                    if output_relative in used_outputs:
                        output_relative = Path(archive_prefix, f"__item_{member_index:08d}", *relative.parts)
                    used_outputs.add(output_relative)
                    archive_id = hashlib.sha256(str(archive_path.resolve()).encode()).hexdigest()[:16]
                    key = f"zip_{archive_id}/{member_index:08d}/" + relative.as_posix()
                    assets.append(TakeoutAsset(
                        key=key,
                        display_name=f"{archive_path.name}:{relative.as_posix()}",
                        archive=archive_path,
                        member=info.filename,
                        info=info,
                        size=info.file_size,
                        kind=kind,
                        taken_date=taken,
                        output_relative=output_relative,
                    ))
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise TakeoutError(msg("takeout.bad_zip", path=archive_path, error=exc)) from exc
    return assets
