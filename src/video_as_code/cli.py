from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import yaml

from . import (
    autoprune, doctor, fetch, fetch_resolve, gallery, gltransitions, gphotos,
    i18n, lumas, mltdata, osp, parts, probe, provenance, proxies, reverse, sources_lock,
    splitrender, timecode, trackpack, upscale, watch, xmlgen,
)
from .errors import KdenliveAsCodeError
from .i18n import msg
from .schema import load_and_resolve, load_yaml


def _yaml_str_representer(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _dump_yaml(data: dict) -> str:
    dumper = yaml.Dumper
    dumper.add_representer(str, _yaml_str_representer)
    return yaml.dump(data, Dumper=dumper, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _run_fetch(
    yaml_path: Path, verify: bool = False, interactive: Optional[bool] = None, prune: bool = False
) -> bool:
    """_run_fetch_one for a project's file - or, for one in several files
    (parts.py), for each of its parts, in its own folder (its own lock,
    its own sources): a fix asked for in the terminal is written into the
    part's file, where the clip is."""
    if parts.has_parts(yaml_path):
        try:
            leaves = parts.register(yaml_path)
        except KdenliveAsCodeError as exc:
            print(str(exc), file=sys.stderr)
            return False
        ok = True
        for leaf in leaves:
            print(msg("cli.fetch_part", part=leaf), file=sys.stderr)
            ok = _run_fetch_one(leaf, verify, interactive, prune) and ok
        return ok
    return _run_fetch_one(yaml_path, verify, interactive, prune)


def _run_fetch_one(
    yaml_path: Path, verify: bool = False, interactive: Optional[bool] = None, prune: bool = False
) -> bool:
    """Shared by `fetch` and `build`/`render --fetch`; False (after
    printing why) if anything couldn't be downloaded.

    In a terminal (or with `interactive`), what couldn't be fetched is
    then put to the user one by one (fetch_resolve.py) and fetch runs
    again with their fixes, until nothing's left that they haven't
    skipped. Elsewhere (scripts, CI) it just fails, listing it all."""
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()

    def _progress(text: str) -> None:
        print(text, file=sys.stderr)

    if interactive and not _offer_upscaler(yaml_path):
        return False

    skipped: set[str] = set()
    while True:
        try:
            result = fetch.fetch(yaml_path, on_progress=_progress, verify=verify, prune=prune)
            break
        except fetch.FetchError as exc:
            print(str(exc), file=sys.stderr)
            pending = [p for p in exc.problems if fetch_resolve.key(p) not in skipped]
            # (a failure with no Problem - a derive: that broke - can't be
            # fixed from here: once only those are left, this gives up)
            if not interactive or not pending:
                return False
            outcome = fetch_resolve.resolve(yaml_path, pending)
            skipped |= outcome.skipped
            if not outcome.changed:
                return False
        except (KdenliveAsCodeError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return False
    print(
        msg(
            "cli.fetch_done",
            downloaded=len(result.downloaded),
            up_to_date=len(result.up_to_date),
            derived=len(result.derived),
        )
    )
    if result.pruned:
        print(msg("cli.fetch_pruned", n=len(result.pruned), mb=round(result.pruned_bytes / 1e6)))
    return True


def _offer_upscaler(yaml_path: Path) -> bool:
    """Before a fetch in a terminal: if some derive: clip needs Real-ESRGAN
    (`upscale:`) and it isn't installed, offer to download it. False only
    if the download was accepted and failed (declining just lets those
    clips fail later, with the message saying how to install it)."""
    try:
        doc = load_yaml(yaml_path)
    except (KdenliveAsCodeError, OSError):
        return True  # fetch itself will report it
    needing = [c for c in doc.bin if c.derive is not None and c.derive.upscale]
    if not needing or upscale.find_binary() is not None or not upscale.can_install():
        return True
    answer = input(
        msg("upscale.ask_install", n=len(needing), mb=upscale.RELEASE_SIZE_MB, path=upscale.install_dir())
    )
    if answer.strip().lower() not in ("", "y", "yes", "s", "si", "sí"):
        return True
    try:
        upscale.install(report=lambda text: print(text, file=sys.stderr))
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return False
    return True


def cmd_install_upscaler(args: argparse.Namespace) -> int:
    try:
        upscale.install(report=lambda text: print(text, file=sys.stderr))
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_install_gl(args: argparse.Namespace) -> int:
    try:
        gltransitions.install(report=lambda text: print(text, file=sys.stderr))
    except (KdenliveAsCodeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _output(yaml_path: Path, args: argparse.Namespace, suffix: str) -> Path:
    """-o, else the project file's name - and the part's, with --part."""
    if args.output:
        return Path(args.output)
    part = getattr(args, "part", None)
    if part:
        return yaml_path.with_name(f"{yaml_path.stem}-{part.strip('/').replace('/', '-')}{suffix}")
    return yaml_path.with_suffix(suffix)


def cmd_fetch(args: argparse.Namespace) -> int:
    return 0 if _run_fetch(Path(args.project), verify=args.verify, prune=args.prune) else 1


def cmd_publish(args: argparse.Namespace) -> int:
    from . import youtube  # (only `publish` needs it)

    yaml_path = Path(args.project)
    try:
        spec = load_yaml(yaml_path).project.youtube
        if spec is None:
            print(msg("youtube.not_configured", path=yaml_path), file=sys.stderr)
            return 1
        youtube.publish(spec, Path(args.video), yaml_path.parent, lambda text: print(text, file=sys.stderr),
                        dry_run=args.dry_run, replace=args.replace, yes=args.yes)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    fmt = args.format or ("openshot" if args.output and Path(args.output).suffix.lower() == ".osp" else "kdenlive")
    output = _output(yaml_path, args, ".osp" if fmt == "openshot" else ".kdenlive")
    if args.fetch and not _run_fetch(yaml_path):
        return 1
    try:
        resolved = load_and_resolve(yaml_path, part=args.part)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    original = resolved
    resolved = _with_proxies(resolved, args.proxy)
    if resolved is None:
        return 1
    _record_use(yaml_path, original, resolved)
    if args.pack_tracks:
        resolved = trackpack.pack_tracks(resolved)
    try:
        if fmt == "openshot":
            for warning in osp.write(resolved, output):
                print(warning, file=sys.stderr)
        else:
            xmlgen.write(resolved, output)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(msg("cli.write_failed", path=output, exc=exc), file=sys.stderr)
        return 1
    print(
        msg(
            "cli.build_done",
            output=output,
            n_tracks=len(resolved.tracks),
            frames=resolved.total_frames,
            fps=resolved.fps.fps,
        )
    )
    return 0


def _record_use(yaml_path: Path, *projects) -> None:
    """Notes that the files these resolved projects read were used today
    (see autoprune.py) - the original and its --proxy version alike."""
    paths: set[Path] = set()
    for project in projects:
        if project is not None:
            paths |= autoprune.used_paths(project)
    autoprune.record_use(yaml_path.parent, paths)
    if parts.has_parts(yaml_path):  # (each part's folder keeps its own record)
        try:
            for leaf in parts.register(yaml_path):
                autoprune.record_use(leaf.parent, paths)
        except KdenliveAsCodeError:
            pass


def _autoprune(yaml_path: Path, needed_by) -> None:
    """Before a render: deletes old files the tool can bring back (see
    autoprune.py), never one this render reads."""
    try:
        files = parts.register(yaml_path) if parts.has_parts(yaml_path) else [yaml_path]
        docs = [load_yaml(f) for f in files]  # (a part's folder has its own sources)
    except KdenliveAsCodeError:
        return
    deleted, freed = [], 0
    for doc in docs:
        days = doc.project.prune_after_days
        d, f = autoprune.prune(doc, autoprune.used_paths(needed_by), days)
        deleted, freed = deleted + d, freed + f
    if deleted:
        print(msg("cli.autopruned", n=len(deleted), days=days, mb=round(freed / 1e6)), file=sys.stderr)


def _with_proxies(resolved, size_text: Optional[str]):
    """`build`/`render --proxy`: `resolved` reading light copies (see
    proxies.py), or None (after saying why) if the size is malformed or a
    copy couldn't be made."""
    if not size_text:
        return resolved
    try:
        size = proxies.parse_size(size_text)
    except ValueError:
        print(msg("cli.proxy_invalid", value=size_text), file=sys.stderr)
        return None
    errors: list[str] = []
    resolved = proxies.apply(resolved, size, errors, on_progress=lambda text: print(text, file=sys.stderr))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return None
    return resolved


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        resolved = load_and_resolve(Path(args.project), part=args.part)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _record_use(Path(args.project), resolved)
    n_clips = sum(len(t.placements) for t in resolved.tracks)
    print(msg("cli.validate_done", n_tracks=len(resolved.tracks), n_clips=n_clips, frames=resolved.total_frames))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    kdenlive_path = Path(args.kdenlive_file)
    output = Path(args.output) if args.output else kdenlive_path.with_suffix(".yaml")
    try:
        result = reverse.import_kdenlive(kdenlive_path)
    except (KdenliveAsCodeError, ValueError) as exc:
        print(msg("cli.import_error", path=kdenlive_path, exc=exc), file=sys.stderr)
        return 1

    try:
        output.write_text(_dump_yaml(result.data), encoding="utf-8")
        for rel, data in result.files.items():  # (a project of parts: each part's file, by the main one)
            part_file = output.parent / rel
            part_file.parent.mkdir(parents=True, exist_ok=True)
            part_file.write_text(_dump_yaml(data), encoding="utf-8")
            print(msg("cli.import_done", output=part_file))
    except OSError as exc:
        print(msg("cli.write_failed", path=output, exc=exc), file=sys.stderr)
        return 1
    print(msg("cli.import_done", output=output))
    for w in result.warnings:
        print(msg("cli.warning_prefix", w=w), file=sys.stderr)
    if result.raw_items:
        print(msg("cli.raw_items_notice", n=len(result.raw_items), output=output))
    return 0


def melt_env_for(resolved, melt: str) -> Optional[dict[str, str]]:
    """The environment melt renders `resolved` in - None (after saying
    why) if it can't be made."""
    # C locale, or decimals like `gain: 2.5`/`speed: 1.08` get truncated
    # under a comma-decimal locale - see probe.melt_env; and, for
    # `film_dust: {style: dots}`, melt's data folder without its dust
    # pictures (with `pictures:`, with the chosen ones) - see mltdata.py
    mlt_data = None
    dust_pictures = mltdata.wanted_pictures(resolved)
    if mltdata.wants_dots(resolved) or dust_pictures:
        data_dir = mltdata.find_data_dir(melt)
        if data_dir is None:
            print(msg("cli.render_no_mlt_data", melt=melt), file=sys.stderr)
            return None
        mlt_data = mltdata.pictures_data_dir(data_dir, mltdata.default_cache_root(), dust_pictures or ())
    return probe.melt_env(mlt_data=mlt_data)


def _stretch(args: argparse.Namespace, resolved) -> Optional[tuple[int, int, str]]:
    """--from/--to as (first, last) frames and a name for the file (`1m20s-1m35s`)
    - None for the whole video. KdenliveAsCodeError if they aren't times within it."""
    start, end = getattr(args, "start", None), getattr(args, "end", None)
    if start is None and end is None:
        return None
    total = resolved.total_frames
    length = watch.clock(resolved.fps.frames_to_seconds(total))
    try:
        start_s = timecode.parse_clock(start) if start is not None else 0.0
        end_s = timecode.parse_clock(end) if end is not None else None
    except ValueError as exc:
        raise KdenliveAsCodeError(msg("cli.stretch_bad_time", time=exc)) from None
    first = resolved.fps.seconds_to_frames(start_s)
    last = resolved.fps.seconds_to_frames(end_s) - 1 if end_s is not None else total - 1
    if first >= total or last >= total or first > last:
        raise KdenliveAsCodeError(msg("cli.stretch_outside", start=start or "0", end=end or length, length=length))
    end_s = resolved.fps.frames_to_seconds(total) if end_s is None else end_s
    return first, last, f"{timecode.clock_name(start_s)}-{timecode.clock_name(end_s)}"


def cmd_render(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    output = _output(yaml_path, args, ".mp4")
    if args.fetch and not _run_fetch(yaml_path):
        return 1

    try:
        resolved = load_and_resolve(yaml_path, part=args.part)
        stretch_and_name = _stretch(args, resolved)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    stretch = stretch_and_name[:2] if stretch_and_name else None
    if stretch_and_name and not args.output:  # (not over the whole video's file)
        output = output.with_name(f"{output.stem}_{stretch_and_name[2]}{output.suffix}")
    frames = stretch[1] - stretch[0] + 1 if stretch else resolved.total_frames

    melt = probe.find_melt()
    if melt is None:
        print(msg("cli.render_missing_melt"), file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)  # (e.g. output/ in a fresh clone)
    original = resolved
    resolved = _with_proxies(resolved, args.proxy)
    if resolved is None:
        return 1
    _record_use(yaml_path, original, resolved)
    if not args.no_prune and not args.part:
        # (with --proxy the originals are kept too: recorded as used above;
        # with --part, the other parts' files aren't read by this render -
        # not a sign that nothing uses them)
        _autoprune(yaml_path, resolved)

    env = melt_env_for(resolved, melt)
    if env is None:
        return 1

    with contextlib.ExitStack() as stack:
        if args.keep_kdenlive:
            kdenlive_path = yaml_path.with_suffix(".kdenlive")
        else:
            tmp_dir = stack.enter_context(tempfile.TemporaryDirectory())
            kdenlive_path = Path(tmp_dir) / (yaml_path.stem + ".kdenlive")

        try:
            xmlgen.write(resolved, kdenlive_path)
        except OSError as exc:
            print(msg("cli.write_failed", path=kdenlive_path, exc=exc), file=sys.stderr)
            return 1
        if args.split:
            try:
                splitrender.render(
                    kdenlive_path,
                    output,
                    resolved.total_frames,
                    melt,
                    yaml_path.parent / splitrender.CACHE_DIR,
                    budget_mb=args.max_mem,
                    report=lambda text: print(text, file=sys.stderr),
                    env=env,
                    stretch=stretch,
                )
            except (KdenliveAsCodeError, OSError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(msg("cli.render_done", output=output, frames=frames, fps=resolved.fps.fps))
            if args.keep_kdenlive:
                print(msg("cli.render_kept_kdenlive", path=kdenlive_path))
            return 0
        source, limits = kdenlive_path, []
        if stretch:
            # melt loads every clip in the file it's given: a copy with
            # only those the stretch plays (splitrender.trim), rendered
            # with the same in=/out= as in the whole
            source = Path(stack.enter_context(tempfile.TemporaryDirectory())) / kdenlive_path.name
            root = ET.parse(kdenlive_path).getroot()
            ET.ElementTree(splitrender.trim(root, *stretch)).write(source, encoding="utf-8", xml_declaration=True)
            limits = [f"in={stretch[0]}", f"out={stretch[1]}"]
        result = subprocess.run(
            [melt, str(source), "-consumer", f"avformat:{output}", *limits],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            print(msg("cli.render_failed", path=output, stderr=result.stderr.strip()), file=sys.stderr)
            return 1

    print(msg("cli.render_done", output=output, frames=frames, fps=resolved.fps.fps))
    if args.keep_kdenlive:
        print(msg("cli.render_kept_kdenlive", path=kdenlive_path))
    return 0


def _megabytes(size: int) -> str:
    return f"{size / 1e6:,.1f} MB" if size < 1e9 else f"{size / 1e9:,.2f} GB"


def cmd_clean(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    if not yaml_path.exists():
        print(msg("validation.yaml_missing", path=yaml_path), file=sys.stderr)
        return 1
    try:
        report = provenance.clean(
            yaml_path, only=args.paths or None, dry_run=True,
            report_progress=lambda text: print(text, file=sys.stderr),
        )
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    groups: dict[tuple, list[provenance.Result]] = {}
    for result in report.results:
        groups.setdefault((result.candidate.group, result.ok, tuple(result.reasons)), []).append(result)
    freed = 0
    for (group, ok, reasons), results in groups.items():
        size = sum(r.size for r in results)
        if ok:
            verb = "clean.would_remove"
            freed += sum(r.size for r in results)
        else:
            verb = "clean.kept"
        print(msg(verb, group=group, files=len(results), size=_megabytes(size)))
        for reason in reasons:
            print(f"    {reason}")
    for folder, (files, size) in sorted(report.unregistered.items()):
        print(msg("clean.unregistered", group=folder, files=files, size=_megabytes(size)))
    if not report.results:
        print(msg("clean.nothing"))
        return 0
    deletable = [r for r in report.results if r.ok]
    print(msg("clean.total_dry" if args.dry_run else "clean.would_free", size=_megabytes(freed)))
    print(msg("clean.warning"), file=sys.stderr)
    if args.dry_run or not deletable:
        return 0
    if not args.yes_i_am_sure and not _confirm_clean(len(deletable), freed):
        print(msg("clean.cancelled"), file=sys.stderr)
        return 1
    provenance.apply(report)
    print(msg("clean.total", size=_megabytes(sum(r.size for r in deletable if r.removed))))
    return 0


def _confirm_clean(files: int, size: int) -> bool:
    """Asks for the typed phrase (not a bare y/n) before deleting for good."""
    if not sys.stdin.isatty():
        print(msg("clean.not_interactive"), file=sys.stderr)
        return False
    print("\n" + msg("clean.confirm_banner", files=files, size=_megabytes(size)).upper(), file=sys.stderr)
    phrase = msg("clean.confirm_phrase")
    try:
        answer = input(msg("clean.confirm_prompt", phrase=phrase) + " ")
    except EOFError:
        return False

    def plain(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text.strip().casefold())
        return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")

    return plain(answer) == plain(phrase)


def cmd_provenance(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    if not yaml_path.exists():
        print(msg("validation.yaml_missing", path=yaml_path), file=sys.stderr)
        return 1
    sources: list[dict] = []
    for name in args.item or []:
        sources.append({"item": name, **({"album": args.album} if args.album else {})})
    sources += [{"url": url} for url in args.url or []]
    sources += [{"link": clip_id} for clip_id in args.link or []]
    sources += [{"file": str(Path(path).resolve())} for path in args.source_file or []]
    if args.reference:
        sources.append({"reference": True})
    try:
        entry = provenance.register(
            yaml_path.resolve().parent, Path(args.path).resolve(), sources,
            str(Path(args.recipe).resolve()) if args.recipe else None, args.command, args.note,
        )
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(msg("provenance.registered", path=entry["path"], n=len(entry["sources"])))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    return doctor.run()


def _persist_takeout_matches(
    result,
    output: Path,
    entries: Optional[list[dict]] = None,
    *,
    move_incremental: bool = False,
) -> Optional[Path]:
    """Extract only matched Takeout ZIP members needed for local sources."""
    selected = entries if entries is not None else result.data.get("bin", [])
    selected = [entry for entry in selected if entry.get("path") in result.asset_candidates]
    if not selected:
        return None
    required_bytes = 0
    output_device = output.parent.stat().st_dev
    for entry in selected:
        candidate = result.asset_candidates[entry["path"]]
        local_path = getattr(candidate, "local_path", None)
        if not (move_incremental and local_path is not None and local_path.stat().st_dev == output_device):
            required_bytes += candidate.size
    free_bytes = shutil.disk_usage(output.parent).free
    if required_bytes + 1024 * 1024 > free_bytes:
        raise OSError(msg("cli.reconstruct_assets_space", needed=round(required_bytes / 1024 / 1024),
                          free=round(free_bytes / 1024 / 1024), path=output.parent))
    base = output.with_name(f"{output.stem}_assets")
    asset_dir = base
    suffix = 2
    while asset_dir.exists():
        asset_dir = base.with_name(f"{base.name}_{suffix}")
        suffix += 1
    asset_dir.mkdir(parents=True, exist_ok=False)
    for entry in selected:
        candidate = result.asset_candidates[entry["path"]]
        local_path = getattr(candidate, "local_path", None)
        if move_incremental and local_path is not None and local_path.stat().st_dev == output_device:
            destination = asset_dir / local_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            local_path.replace(destination)
        else:
            destination = candidate.copy_to(asset_dir)
        entry["path"] = str(destination.resolve())
    return asset_dir


class _TakeoutSessionError(ValueError):
    pass


def _load_takeout_session(session_dir: Path, video_path: Path, takeout_module) -> list:
    manifest_path = session_dir / "session.json"
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("unsupported session format")
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(msg("cli.reconstruct_takeout_session_invalid", path=manifest_path, error=exc)) from exc
    stat = video_path.stat()
    video_state = manifest.get("video", {})
    if not isinstance(video_state, dict):
        raise _TakeoutSessionError(msg("cli.reconstruct_takeout_session_video", path=session_dir))
    if (video_state.get("path") != str(video_path.resolve()) or video_state.get("size") != stat.st_size
            or video_state.get("mtime_ns") != stat.st_mtime_ns):
        raise _TakeoutSessionError(msg("cli.reconstruct_takeout_session_video", path=session_dir))
    candidates = []
    try:
        for item in manifest["candidates"]:
            path = session_dir / "assets" / item["path"]
            if not path.is_file():
                raise _TakeoutSessionError(msg("cli.reconstruct_takeout_session_missing", path=path))
            candidates.append(takeout_module.LocalTakeoutAsset(
                key=item["key"], display_name=item["display_name"], local_path=path,
                size=path.stat().st_size, kind=item["kind"],
                taken_date=dt.date.fromisoformat(item["taken_date"]) if item["taken_date"] else None,
            ))
    except _TakeoutSessionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(msg("cli.reconstruct_takeout_session_invalid", path=manifest_path, error=exc)) from exc
    return candidates


def _save_takeout_session(session_dir: Path, video_path: Path, result, takeout_module) -> dict:
    assets_dir = session_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    needed_bytes = 0
    for key, candidate in result.asset_candidates.items():
        suffix = Path(candidate.display_name).suffix.lower() or ".media"
        destination = assets_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:20] + suffix)
        existing_size = destination.stat().st_size if destination.is_file() else 0
        if existing_size != candidate.size:
            needed_bytes += max(0, candidate.size - existing_size)
    free_bytes = shutil.disk_usage(assets_dir).free
    reserve_bytes = 1024 * 1024
    if needed_bytes + reserve_bytes > free_bytes:
        raise takeout_module.TakeoutError(msg(
            "cli.reconstruct_takeout_session_space",
            needed=round(needed_bytes / 1024 / 1024),
            free=round(free_bytes / 1024 / 1024),
            path=assets_dir,
        ))
    persisted = {}
    candidate_rows = []
    kept_paths = set()
    for key, candidate in result.asset_candidates.items():
        suffix = Path(candidate.display_name).suffix.lower() or ".media"
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20] + suffix
        destination = assets_dir / filename
        source_path = getattr(candidate, "local_path", None)
        if not (destination.is_file() and destination.stat().st_size == candidate.size):
            with candidate.open_stream() as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        kept_paths.add(filename)
        row = {
            "key": key, "display_name": candidate.display_name, "kind": candidate.kind,
            "taken_date": candidate.taken_date.isoformat() if candidate.taken_date else None,
            "path": filename,
        }
        candidate_rows.append(row)
        persisted[key] = takeout_module.LocalTakeoutAsset(
            key=key, display_name=candidate.display_name, local_path=destination,
            size=destination.stat().st_size, kind=candidate.kind, taken_date=candidate.taken_date,
        )
    for path in assets_dir.iterdir():
        if path.is_file() and path.name not in kept_paths:
            path.unlink()
    stat = video_path.stat()
    manifest = {
        "version": 1,
        "video": {"path": str(video_path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "candidates": candidate_rows,
    }
    manifest_path = session_dir / "session.json"
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return persisted


def _reuse_takeout_session_assets(session_dir: Path, result, takeout_module) -> None:
    """Reuse complete deterministic candidate copies left by an interrupted save."""
    assets_dir = session_dir / "assets"
    for key, candidate in list(result.asset_candidates.items()):
        suffix = Path(candidate.display_name).suffix.lower() or ".media"
        path = assets_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:20] + suffix)
        if path.is_file() and path.stat().st_size == candidate.size:
            result.asset_candidates[key] = takeout_module.LocalTakeoutAsset(
                key=key, display_name=candidate.display_name, local_path=path,
                size=candidate.size, kind=candidate.kind, taken_date=candidate.taken_date,
            )


def cmd_reconstruct(args: argparse.Namespace) -> int:
    video_path = Path(args.video)
    takeout = getattr(args, "takeout", None)
    takeout_part = getattr(args, "takeout_part", None)
    takeout_session = getattr(args, "takeout_session", None)
    takeout_final = getattr(args, "takeout_final", False)
    publish_photos = getattr(args, "publish_to_photos", False)
    if takeout == "":
        print(msg("cli.reconstruct_takeout_guide"), file=sys.stderr)
        return 0
    assets_arg = takeout or args.assets_dir
    assets_dir = Path(assets_arg) if assets_arg else None
    output = Path(args.output) if args.output else video_path.with_suffix(".yaml")

    if publish_photos and not takeout:
        if not takeout_part:
            print(msg("cli.reconstruct_publish_needs_takeout"), file=sys.stderr)
            return 2
    if args.album and (assets_dir is not None or publish_photos):
        print(msg("cli.reconstruct_choose_source"), file=sys.stderr)
        return 2
    if takeout and (args.assets_dir or takeout_part):
        print(msg("cli.reconstruct_choose_source"), file=sys.stderr)
        return 2
    if takeout_part and (takeout_session is None or takeout or args.assets_dir or args.album):
        print(msg("cli.reconstruct_takeout_incremental_usage"), file=sys.stderr)
        return 2
    if takeout_final and not takeout_part:
        print(msg("cli.reconstruct_takeout_final_needs_part"), file=sys.stderr)
        return 2
    if takeout_session and not takeout_part:
        print(msg("cli.reconstruct_takeout_incremental_usage"), file=sys.stderr)
        return 2
    if not args.album and assets_dir is None and not takeout_part:
        print(msg("cli.reconstruct_assets_required", video=shlex.quote(str(video_path))), file=sys.stderr)
        return 2
    preferred_from = getattr(args, "takeout_from", None)
    preferred_to = getattr(args, "takeout_to", None)
    if (preferred_from or preferred_to) and not (takeout or takeout_part):
        print(msg("cli.reconstruct_takeout_dates_need_zip"), file=sys.stderr)
        return 2
    if preferred_from and preferred_to and preferred_from > preferred_to:
        print(msg("cli.reconstruct_takeout_date_order"), file=sys.stderr)
        return 2

    try:
        from . import reconstruct, takeout as takeout_module
    except ImportError:
        print(msg("cli.reconstruct_missing_extra"), file=sys.stderr)
        return 1

    if publish_photos and not takeout_part:
        from . import photos_api
        try:
            photos_api.check_client()
        except photos_api.PhotosApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    def _progress(text: str) -> None:
        print(text, file=sys.stderr)

    zip_takeout = False
    incremental_takeout = bool(takeout_part)
    incremental_finalize = False
    incremental_session_dir: Optional[Path] = None
    try:
        if takeout_part:
            part_path = Path(takeout_part).expanduser()
            session_dir = Path(takeout_session).expanduser()
            incremental_session_dir = session_dir
            if not part_path.is_file():
                raise takeout_module.TakeoutError(msg("cli.reconstruct_takeout_part_invalid", path=part_path))
            if part_path.suffix.lower() != ".zip":
                raise takeout_module.TakeoutError(msg("cli.reconstruct_takeout_part_zip", path=part_path))
            session_dir.mkdir(parents=True, exist_ok=True)
            previous = _load_takeout_session(session_dir, video_path, takeout_module)
            current = takeout_module.scan_zip_folder(
                part_path.parent, reconstruct.guess_kind, archive_paths=[part_path],
                prefer_from=preferred_from, prefer_to=preferred_to,
            )
            if not current and not previous:
                raise takeout_module.TakeoutError(msg("takeout.no_media"))
            candidates = previous + current
            if preferred_from or preferred_to:
                lower = preferred_from or dt.date.min
                upper = preferred_to or dt.date.max

                def _incremental_priority(candidate):
                    if candidate.taken_date is None:
                        return (1, 0)
                    if lower <= candidate.taken_date <= upper:
                        return (0, 0)
                    distance = (lower - candidate.taken_date).days if candidate.taken_date < lower else (candidate.taken_date - upper).days
                    return (2, distance)

                candidates.sort(key=_incremental_priority)
            print(msg("cli.reconstruct_takeout_incremental_scanning", path=part_path.name,
                      previous=len(previous), current=len(current)), file=sys.stderr)
            result = reconstruct.reconstruct(video_path, None, on_progress=_progress,
                                              asset_candidates=candidates)
            zip_takeout = True
            video_unmatched = [item for item in result.data.get("x_reconstruct_unmatched", [])
                               if str(item.get("where", "")).startswith("video ")]
            incremental_finalize = not video_unmatched or takeout_final
            if not incremental_finalize:
                result.asset_candidates = _save_takeout_session(session_dir, video_path, result, takeout_module)
                for unmatched in result.data.get("x_reconstruct_unmatched", []):
                    guess = unmatched.get("best_guess")
                    if guess in result.asset_candidates:
                        unmatched["best_guess"] = result.asset_candidates[guess].display_name
                matched = sum(len(track.get("clips", [])) for track in result.data.get("timeline", {}).get("tracks", [])
                              if track.get("type") == "video")
                print(msg("cli.reconstruct_takeout_incremental_progress", matched=matched,
                          pending=len(video_unmatched), session=session_dir), file=sys.stderr)
                return 0
            # Final output needs only assets used by confident clips. Avoid
            # copying every near-match into the session, and reuse any complete
            # files left behind by an earlier interrupted session save.
            _reuse_takeout_session_assets(session_dir, result, takeout_module)
            for unmatched in result.data.get("x_reconstruct_unmatched", []):
                guess = unmatched.get("best_guess")
                if guess in result.asset_candidates:
                    unmatched["best_guess"] = result.asset_candidates[guess].display_name
        elif args.album:
            with tempfile.TemporaryDirectory(prefix="video-as-code-album-") as tmp:
                album_dir = Path(tmp)
                staged: dict[str, tuple[gphotos.AlbumItem, str]] = {}
                print(msg("cli.reconstruct_album_loading"), file=sys.stderr)
                items = gphotos.list_album(args.album)
                for index, item in enumerate(items, 1):
                    name = gphotos.original_filename(item)
                    staged_name = f"{item.media_id}{Path(name).suffix.lower()}"
                    print(msg("cli.reconstruct_album_item", i=index, n=len(items)), file=sys.stderr)
                    gphotos.download(item, album_dir / staged_name)
                    staged[staged_name] = (item, name)

                result = reconstruct.reconstruct(video_path, album_dir, on_progress=_progress)
                result.data["project"]["sources"] = {"album": args.album}
                staged_names = {Path(name).stem: original for name, (_, original) in staged.items()}
                for entry in result.data.get("bin", []):
                    item_record = staged.get(Path(entry.get("path", "")).name)
                    if item_record is None:
                        continue
                    item, original_name = item_record
                    entry.pop("path", None)
                    entry["source"] = {
                        "name": original_name,
                        "taken": sources_lock.taken_local(item.taken_ms, item.tz_offset_ms),
                        "size": [item.width, item.height],
                        "id": item.media_id,
                    }
                for unmatched in result.data.get("x_reconstruct_unmatched", []):
                    guess = unmatched.get("best_guess")
                    if guess:
                        unmatched["best_guess"] = staged_names.get(Path(guess).stem, guess)
        else:
            assert assets_dir is not None
            if not assets_dir.is_dir():
                print(msg("cli.reconstruct_assets_path_invalid", path=assets_dir), file=sys.stderr)
                return 2
            archive_paths = sorted(path for path in assets_dir.rglob("*")
                                   if path.is_file() and path.suffix.lower() == ".zip") if takeout else []
            if archive_paths:
                candidates = takeout_module.scan_zip_folder(
                    assets_dir, reconstruct.guess_kind, archive_paths=archive_paths,
                    prefer_from=preferred_from, prefer_to=preferred_to,
                )
                if not candidates:
                    raise takeout_module.TakeoutError(msg("takeout.no_media"))
                if preferred_from or preferred_to:
                    lower = preferred_from or dt.date.min
                    upper = preferred_to or dt.date.max

                    def _candidate_priority(candidate):
                        if candidate.taken_date is None:
                            return (1, 0)
                        if lower <= candidate.taken_date <= upper:
                            return (0, 0)
                        distance = (lower - candidate.taken_date).days if candidate.taken_date < lower else (candidate.taken_date - upper).days
                        return (2, distance)

                    candidates.sort(key=_candidate_priority)
                    print(msg("cli.reconstruct_takeout_priority", from_date=preferred_from or "…",
                              to_date=preferred_to or "…"), file=sys.stderr)
                print(msg("cli.reconstruct_takeout_zip_count", n=len(candidates)), file=sys.stderr)
                result = reconstruct.reconstruct(video_path, None, on_progress=_progress,
                                                  asset_candidates=candidates)
                zip_takeout = True
                del candidates
                for unmatched in result.data.get("x_reconstruct_unmatched", []):
                    guess = unmatched.get("best_guess")
                    if guess in result.asset_candidates:
                        unmatched["best_guess"] = result.asset_candidates[guess].display_name
            else:
                if preferred_from or preferred_to:
                    print(msg("cli.reconstruct_takeout_dates_need_zip"), file=sys.stderr)
                    return 2
                if takeout and not reconstruct.asset_files(assets_dir):
                    raise takeout_module.TakeoutError(msg("takeout.no_media"))
                result = reconstruct.reconstruct(video_path, assets_dir, on_progress=_progress)
    except (reconstruct.ReconstructError, gphotos.GPhotosError, takeout_module.TakeoutError, OSError, ValueError) as exc:
        print(msg("cli.reconstruct_error", path=video_path, exc=exc), file=sys.stderr)
        return 1

    if publish_photos and incremental_takeout:
        from . import photos_api
        try:
            photos_api.check_client()
        except photos_api.PhotosApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    entries = result.data.get("bin", [])
    if zip_takeout and not publish_photos:
        try:
            asset_dir = _persist_takeout_matches(result, output, move_incremental=incremental_takeout)
            if asset_dir:
                print(msg("cli.reconstruct_local_assets", path=asset_dir), file=sys.stderr)
        except (OSError, KeyError) as exc:
            print(msg("cli.reconstruct_error", path=video_path, exc=exc), file=sys.stderr)
            return 1

    if publish_photos:
        # For ordinary folders, keep a local draft before upload. ZIP mode
        # defers any persistent extraction until it is actually needed.
        if not zip_takeout:
            try:
                output.write_text(_dump_yaml(result.data), encoding="utf-8")
            except OSError as exc:
                print(msg("cli.write_failed", path=output, exc=exc), file=sys.stderr)
                return 1
        photos_extensions = {".avif", ".bmp", ".gif", ".heic", ".ico", ".jpg", ".jpeg", ".png",
                             ".tif", ".tiff", ".webp", ".3gp", ".3g2", ".asf", ".avi", ".divx",
                             ".m2t", ".m2ts", ".m4v", ".mkv", ".mmv", ".mod", ".mov", ".mp4",
                             ".mpg", ".mts", ".tod", ".wmv"}

        def _source(entry):
            key = entry.get("path", "")
            if zip_takeout and key in result.asset_candidates:
                return result.asset_candidates[key]
            return Path(key)

        def _source_name(source):
            return Path(source.member).name if hasattr(source, "member") else source.name

        def _source_size(source):
            return source.size if hasattr(source, "size") else source.stat().st_size

        def _source_kind(source):
            if hasattr(source, "kind"):
                return source.kind
            return reconstruct.guess_kind(source)

        upload_entries = []
        for entry in entries:
            source = _source(entry)
            suffix = Path(_source_name(source)).suffix.lower()
            if _source_kind(source) in ("image", "video") and suffix in photos_extensions:
                upload_entries.append((entry, source))
        local_entries = [entry for entry in entries if entry not in [item[0] for item in upload_entries]]

        if not upload_entries:
            print(msg("cli.reconstruct_no_matches"), file=sys.stderr)
            if zip_takeout:
                try:
                    _persist_takeout_matches(result, output, move_incremental=incremental_takeout)
                except (OSError, KeyError) as exc:
                    print(msg("cli.reconstruct_error", path=video_path, exc=exc), file=sys.stderr)
                    return 1
        elif not sys.stdin.isatty():
            print(msg("cli.reconstruct_publish_interactive"), file=sys.stderr)
            if zip_takeout:
                try:
                    _persist_takeout_matches(result, output, move_incremental=incremental_takeout)
                    output.write_text(_dump_yaml(result.data), encoding="utf-8")
                except (OSError, KeyError) as exc:
                    print(msg("cli.reconstruct_error", path=video_path, exc=exc), file=sys.stderr)
                    return 1
            return 2
        else:
            try:
                total_bytes = sum(_source_size(source) for _, source in upload_entries)
                print(msg("cli.reconstruct_publish_summary", n=len(upload_entries), mb=round(total_bytes / 1e6),
                          unmatched=len(result.data.get("x_reconstruct_unmatched", [])),
                          local=len(local_entries)), file=sys.stderr)
                for _, source in upload_entries:
                    print(msg("cli.reconstruct_upload_candidate", name=_source_name(source),
                              mb=round(_source_size(source) / 1e6)), file=sys.stderr)
                answer = input(msg("cli.reconstruct_publish_confirm"))
                if answer.strip().lower() not in ("s", "si", "sí", "y", "yes"):
                    if zip_takeout:
                        _persist_takeout_matches(result, output, move_incremental=incremental_takeout)
                    print(msg("cli.reconstruct_upload_declined"), file=sys.stderr)
                else:
                    token = photos_api.access_token(_progress)
                    api = photos_api.Api(token)
                    album = api.create_album(msg("cli.reconstruct_album_title", title=video_path.stem))
                    uploaded: list[tuple[str, str]] = []
                    for index, (_entry, source) in enumerate(upload_entries, 1):
                        filename = _source_name(source)
                        print(msg("cli.reconstruct_uploading", i=index, n=len(upload_entries), name=filename),
                              file=sys.stderr)
                        if hasattr(source, "open_stream"):
                            with source.open_stream() as stream:
                                upload_token = api.upload_stream(filename, source.size, stream)
                        else:
                            upload_token = api.upload(source)
                        uploaded.append((filename, upload_token))
                    created = api.add_to_album(album["id"], uploaded)
                    print(msg("cli.reconstruct_album_created", url=album.get("productUrl", "https://photos.google.com/")),
                          file=sys.stderr)
                    print(msg("cli.reconstruct_share_instructions"), file=sys.stderr)
                    share_url = input(msg("cli.reconstruct_share_prompt")).strip()
                    album_items = gphotos.list_album(share_url)
                    by_id = {item.media_id: item for item in album_items}
                    if any(not item.get("id") or item["id"] not in by_id for item in created):
                        raise gphotos.GPhotosError(msg("cli.reconstruct_album_items_missing"))
                    remote_sources = []
                    for (entry, _source_ref), media in zip(upload_entries, created):
                        item = by_id[media["id"]]
                        remote_sources.append((entry, {
                            "name": gphotos.original_filename(item),
                            "taken": sources_lock.taken_local(item.taken_ms, item.tz_offset_ms),
                            "size": [item.width, item.height], "id": item.media_id,
                        }))
                    if zip_takeout and local_entries:
                        _persist_takeout_matches(result, output, local_entries,
                                                 move_incremental=incremental_takeout)
                    result.data["project"]["sources"] = {"album": share_url}
                    for entry, source_record in remote_sources:
                        entry.pop("path", None)
                        entry["source"] = source_record
            except (photos_api.PhotosApiError, gphotos.GPhotosError, takeout_module.TakeoutError, OSError, KeyError) as exc:
                print(msg("cli.reconstruct_error", path=video_path, exc=exc), file=sys.stderr)
                if zip_takeout:
                    try:
                        _persist_takeout_matches(result, output, move_incremental=incremental_takeout)
                        output.write_text(_dump_yaml(result.data), encoding="utf-8")
                    except (takeout_module.TakeoutError, OSError, KeyError) as fallback_exc:
                        print(msg("cli.reconstruct_error", path=video_path, exc=fallback_exc), file=sys.stderr)
                print(msg("cli.reconstruct_local_fallback", output=output), file=sys.stderr)
                return 1

    try:
        output.write_text(_dump_yaml(result.data), encoding="utf-8")
    except OSError as exc:
        print(msg("cli.write_failed", path=output, exc=exc), file=sys.stderr)
        return 1
    if incremental_finalize and incremental_session_dir is not None:
        # The finalized YAML now owns local matched media under *_assets, or
        # refers to the remote album. Session candidates are no longer needed.
        shutil.rmtree(incremental_session_dir / "assets", ignore_errors=True)
        (incremental_session_dir / "session.json").unlink(missing_ok=True)
    print(msg("cli.reconstruct_done", output=output))
    for w in result.warnings:
        print(msg("cli.warning_prefix", w=w), file=sys.stderr)
    unmatched = result.data.get("x_reconstruct_unmatched")
    if unmatched:
        print(msg("cli.reconstruct_unmatched_notice", n=len(unmatched), output=output))
    return 0


def cmd_transitions(args: argparse.Namespace) -> int:
    """Every `luma:` a wipe can name, one per line."""
    if lumas.openshot_dir() is None:
        print(msg("cli.transitions_no_openshot", env=lumas.OPENSHOT_DIR_ENV), file=sys.stderr)
    for name, _ in lumas.all_lumas():
        print(name)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """The preview rendered again on every save, played from where it
    changed (watch.py)."""
    yaml_path = Path(args.project)
    if not yaml_path.is_file():
        print(msg("validation.yaml_missing", path=yaml_path), file=sys.stderr)
        return 1
    try:
        watch.run(yaml_path, part=args.part, proxy=None if args.full else args.proxy, play=not args.no_play,
                  report=lambda text: print(text, file=sys.stderr))
    except KeyboardInterrupt:
        print(file=sys.stderr)
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    """The project YAML's JSON Schema (yamlschema.py), for an editor."""
    from . import yamlschema

    if args.output is None:
        sys.stdout.write(yamlschema.shipped())
        return 0
    output = Path(args.output).resolve()
    output.write_text(yamlschema.shipped(), encoding="utf-8")
    print(msg("cli.schema_done", path=output, url=yamlschema.URL))
    return 0


def cmd_gallery(args: argparse.Namespace) -> int:
    """A page with every transition and effect, opened in the browser
    (gallery.py)."""
    own = Path(args.with_file) if args.with_file else None
    if own is not None and not own.is_file():
        print(msg("cli.gallery_with_missing", path=own), file=sys.stderr)
        return 1
    try:
        page = gallery.build(own=own.resolve() if own else None, on_progress=lambda text: print(text, file=sys.stderr))
    except (KdenliveAsCodeError, OSError) as exc:
        print(msg("cli.gallery_failed", exc=exc), file=sys.stderr)
        return 1
    print(msg("cli.gallery_done", path=page))
    if not args.no_open:
        import webbrowser

        webbrowser.open(page.as_uri())
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    """The documentation as a local web, with the gallery in it, opened in
    the browser (docs.py)."""
    from . import docs

    own = Path(args.with_file) if args.with_file else None
    if own is not None and not own.is_file():
        print(msg("cli.gallery_with_missing", path=own), file=sys.stderr)
        return 1
    if own is not None and args.public:
        print(msg("cli.docs_public_with"), file=sys.stderr)
        return 1
    progress = lambda text: print(text, file=sys.stderr)  # noqa: E731
    try:
        if args.public:
            page = docs.publish(Path(args.public).resolve(), on_progress=progress)
        else:
            page = docs.build(own=own.resolve() if own else None, on_progress=progress)
    except (KdenliveAsCodeError, OSError) as exc:
        print(msg("cli.docs_failed", exc=exc), file=sys.stderr)
        return 1
    print(msg("cli.docs_done", path=page))
    if not args.no_open:
        import webbrowser

        webbrowser.open(page.as_uri())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-as-code", description=msg("cli.help.description"))
    parser.add_argument(
        "--lang",
        choices=sorted(i18n.CATALOGS),
        help=msg("cli.help.lang"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help=msg("cli.help.build"))
    p_build.add_argument("project", help=msg("cli.help.project_path"))
    p_build.add_argument("-o", "--output", help=msg("cli.help.build_output"))
    p_build.add_argument("--format", choices=["kdenlive", "openshot"], help=msg("cli.help.build_format"))
    p_build.add_argument("--pack-tracks", action="store_true", help=msg("cli.help.pack_tracks"))
    p_build.add_argument("--fetch", action="store_true", help=msg("cli.help.fetch_first"))
    p_build.add_argument("--proxy", metavar="WxH", help=msg("cli.help.proxy"))
    p_build.add_argument("--part", metavar="PATH", help=msg("cli.help.part"))
    p_build.set_defaults(func=cmd_build)

    p_validate = sub.add_parser("validate", help=msg("cli.help.validate"))
    p_validate.add_argument("project", help=msg("cli.help.project_path"))
    p_validate.add_argument("--part", metavar="PATH", help=msg("cli.help.part"))
    p_validate.set_defaults(func=cmd_validate)

    p_import = sub.add_parser("import", help=msg("cli.help.import"))
    p_import.add_argument("kdenlive_file", help=msg("cli.help.kdenlive_path"))
    p_import.add_argument("-o", "--output", help=msg("cli.help.import_output"))
    p_import.set_defaults(func=cmd_import)

    p_render = sub.add_parser("render", help=msg("cli.help.render"))
    p_render.add_argument("project", help=msg("cli.help.project_path"))
    p_render.add_argument("-o", "--output", help=msg("cli.help.render_output"))
    p_render.add_argument("-k", "--keep-kdenlive", action="store_true", help=msg("cli.help.keep_kdenlive"))
    p_render.add_argument("--fetch", action="store_true", help=msg("cli.help.fetch_first"))
    p_render.add_argument("--split", action="store_true", help=msg("cli.help.render_split"))
    p_render.add_argument("--max-mem", type=int, metavar="MB", help=msg("cli.help.render_max_mem"))
    p_render.add_argument("--proxy", metavar="WxH", help=msg("cli.help.proxy"))
    p_render.add_argument("--part", metavar="PATH", help=msg("cli.help.part"))
    p_render.add_argument("--no-prune", action="store_true", help=msg("cli.help.no_prune"))
    p_render.add_argument("--from", dest="start", metavar="TIME", help=msg("cli.help.render_from"))
    p_render.add_argument("--to", dest="end", metavar="TIME", help=msg("cli.help.render_to"))
    p_render.set_defaults(func=cmd_render)

    p_fetch = sub.add_parser("fetch", help=msg("cli.help.fetch"))
    p_fetch.add_argument("project", help=msg("cli.help.project_path"))
    p_fetch.add_argument("--verify", action="store_true", help=msg("cli.help.fetch_verify"))
    p_fetch.add_argument("--prune", action="store_true", help=msg("cli.help.fetch_prune"))
    p_fetch.set_defaults(func=cmd_fetch)

    p_publish = sub.add_parser("publish", help=msg("cli.help.publish"))
    p_publish.add_argument("project", help=msg("cli.help.project_path"))
    p_publish.add_argument("video", help=msg("cli.help.publish_video"))
    p_publish.add_argument("--dry-run", action="store_true", help=msg("cli.help.publish_dry_run"))
    p_publish.add_argument("--replace", action="store_true", help=msg("cli.help.publish_replace"))
    p_publish.add_argument("--yes", "-y", action="store_true", help=msg("cli.help.publish_yes"))
    p_publish.set_defaults(func=cmd_publish)

    p_upscaler = sub.add_parser("install-upscaler", help=msg("cli.help.install_upscaler"))
    p_upscaler.set_defaults(func=cmd_install_upscaler)

    p_install_gl = sub.add_parser("install-gl-transitions", help=msg("cli.help.install_gl"))
    p_install_gl.set_defaults(func=cmd_install_gl)

    p_transitions = sub.add_parser("transitions", help=msg("cli.help.transitions"))
    p_transitions.set_defaults(func=cmd_transitions)

    p_watch = sub.add_parser("watch", help=msg("cli.help.watch"))
    p_watch.add_argument("project", help=msg("cli.help.project_path"))
    p_watch.add_argument("--proxy", metavar="WxH", default=watch.DEFAULT_PROXY, help=msg("cli.help.watch_proxy"))
    p_watch.add_argument("--full", action="store_true", help=msg("cli.help.watch_full"))
    p_watch.add_argument("--part", metavar="PATH", help=msg("cli.help.part"))
    p_watch.add_argument("--no-play", action="store_true", help=msg("cli.help.watch_no_play"))
    p_watch.set_defaults(func=cmd_watch)

    p_schema = sub.add_parser("schema", help=msg("cli.help.schema"))
    p_schema.add_argument("-o", "--output", metavar="FILE", help=msg("cli.help.schema_output"))
    p_schema.set_defaults(func=cmd_schema)
    p_doctor = sub.add_parser("doctor", help=msg("cli.help.doctor"))
    p_doctor.set_defaults(func=cmd_doctor)

    p_gallery = sub.add_parser("gallery", help=msg("cli.help.gallery"))
    p_gallery.add_argument("--with", dest="with_file", metavar="FILE", help=msg("cli.help.gallery_with"))
    p_gallery.add_argument("--no-open", action="store_true", help=msg("cli.help.gallery_no_open"))
    p_gallery.set_defaults(func=cmd_gallery)

    p_docs = sub.add_parser("docs", help=msg("cli.help.docs"))
    p_docs.add_argument("--with", dest="with_file", metavar="FILE", help=msg("cli.help.docs_with"))
    p_docs.add_argument("--no-open", action="store_true", help=msg("cli.help.docs_no_open"))
    p_docs.add_argument("--public", metavar="FOLDER", help=msg("cli.help.docs_public"))
    p_docs.set_defaults(func=cmd_docs)

    p_clean = sub.add_parser("clean", help=msg("cli.help.clean"))
    p_clean.add_argument("project", help=msg("cli.help.project_path"))
    p_clean.add_argument("paths", nargs="*", metavar="PATH", help=msg("cli.help.clean_paths"))
    p_clean.add_argument("--dry-run", action="store_true", help=msg("cli.help.clean_dry_run"))
    p_clean.add_argument("--yes-i-am-sure", action="store_true", help=msg("cli.help.clean_yes"))
    p_clean.set_defaults(func=cmd_clean)

    p_provenance = sub.add_parser("provenance", help=msg("cli.help.provenance"))
    p_provenance.add_argument("project", help=msg("cli.help.project_path"))
    p_provenance.add_argument("path", help=msg("cli.help.provenance_path"))
    p_provenance.add_argument("--item", action="append", metavar="NAME", help=msg("cli.help.provenance_item"))
    p_provenance.add_argument("--album", metavar="URL", help=msg("cli.help.provenance_album"))
    p_provenance.add_argument("--url", action="append", help=msg("cli.help.provenance_url"))
    p_provenance.add_argument("--reference", action="store_true", help=msg("cli.help.provenance_reference"))
    p_provenance.add_argument("--link", action="append", metavar="BIN_ID", help=msg("cli.help.provenance_link"))
    p_provenance.add_argument("--source-file", action="append", metavar="PATH", help=msg("cli.help.provenance_file"))
    p_provenance.add_argument("--recipe", metavar="PATH", help=msg("cli.help.provenance_recipe"))
    p_provenance.add_argument("--command", help=msg("cli.help.provenance_command"))
    p_provenance.add_argument("--note", help=msg("cli.help.provenance_note"))
    p_provenance.set_defaults(func=cmd_provenance)

    p_reconstruct = sub.add_parser("reconstruct", help=msg("cli.help.reconstruct"))
    p_reconstruct.add_argument("video", help=msg("cli.help.reconstruct_video"))
    p_reconstruct.add_argument("assets_dir", nargs="?", help=msg("cli.help.reconstruct_assets_dir"))
    p_reconstruct.add_argument("--album", help=msg("cli.help.reconstruct_album"))
    p_reconstruct.add_argument("--takeout", nargs="?", const="", metavar="DIR", help=msg("cli.help.reconstruct_takeout"))
    p_reconstruct.add_argument("--takeout-from", type=dt.date.fromisoformat, metavar="YYYY-MM-DD",
                               help=msg("cli.help.reconstruct_takeout_from"))
    p_reconstruct.add_argument("--takeout-to", type=dt.date.fromisoformat, metavar="YYYY-MM-DD",
                               help=msg("cli.help.reconstruct_takeout_to"))
    p_reconstruct.add_argument("--takeout-part", metavar="ZIP", help=msg("cli.help.reconstruct_takeout_part"))
    p_reconstruct.add_argument("--takeout-session", metavar="DIR", help=msg("cli.help.reconstruct_takeout_session"))
    p_reconstruct.add_argument("--takeout-final", action="store_true", help=msg("cli.help.reconstruct_takeout_final"))
    p_reconstruct.add_argument("--publish-to-photos", action="store_true", help=msg("cli.help.reconstruct_publish_photos"))
    p_reconstruct.add_argument("-o", "--output", help=msg("cli.help.reconstruct_output"))
    p_reconstruct.set_defaults(func=cmd_reconstruct)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `--lang` has to be applied before `build_parser()`, since its help
    # text is already localized (msg() calls) at construction time - an
    # ordinary argparse argument couldn't take effect early enough. A
    # lightweight pre-parser peels it off; the real parser also declares
    # `--lang` (see build_parser) purely so it shows up in `--help`.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=sorted(i18n.CATALOGS))
    pre_args, remaining_argv = pre.parse_known_args(argv)
    if pre_args.lang:
        i18n.set_locale(pre_args.lang)

    parser = build_parser()
    args = parser.parse_args(remaining_argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
