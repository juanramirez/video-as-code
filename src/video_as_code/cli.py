from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import yaml

from . import autoprune, doctor, timecode, fetch, fetch_resolve, gallery, gltransitions, i18n, lumas, osp, parts, watch, mltdata, probe, proxies, reverse, splitrender, trackpack, upscale, xmlgen
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


def cmd_doctor(args: argparse.Namespace) -> int:
    return doctor.run()


def cmd_reconstruct(args: argparse.Namespace) -> int:
    video_path = Path(args.video)
    assets_dir = Path(args.assets_dir)
    output = Path(args.output) if args.output else video_path.with_suffix(".yaml")

    try:
        from . import reconstruct
    except ImportError:
        print(msg("cli.reconstruct_missing_extra"), file=sys.stderr)
        return 1

    def _progress(text: str) -> None:
        print(text, file=sys.stderr)

    try:
        result = reconstruct.reconstruct(video_path, assets_dir, on_progress=_progress)
    except reconstruct.ReconstructError as exc:
        print(msg("cli.reconstruct_error", path=video_path, exc=exc), file=sys.stderr)
        return 1

    try:
        output.write_text(_dump_yaml(result.data), encoding="utf-8")
    except OSError as exc:
        print(msg("cli.write_failed", path=output, exc=exc), file=sys.stderr)
        return 1
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

    p_reconstruct = sub.add_parser("reconstruct", help=msg("cli.help.reconstruct"))
    p_reconstruct.add_argument("video", help=msg("cli.help.reconstruct_video"))
    p_reconstruct.add_argument("assets_dir", help=msg("cli.help.reconstruct_assets_dir"))
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
