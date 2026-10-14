from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from . import autoprune, fetch, fetch_resolve, i18n, probe, proxies, reverse, splitrender, trackpack, upscale, xmlgen
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


def cmd_fetch(args: argparse.Namespace) -> int:
    return 0 if _run_fetch(Path(args.project), verify=args.verify, prune=args.prune) else 1


def cmd_build(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    output = Path(args.output) if args.output else yaml_path.with_suffix(".kdenlive")
    if args.fetch and not _run_fetch(yaml_path):
        return 1
    try:
        resolved = load_and_resolve(yaml_path)
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
        xmlgen.write(resolved, output)
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


def _autoprune(yaml_path: Path, needed_by) -> None:
    """Before a render: deletes old files the tool can bring back (see
    autoprune.py), never one this render reads."""
    try:
        doc = load_yaml(yaml_path)
    except KdenliveAsCodeError:
        return
    days = doc.project.prune_after_days
    deleted, freed = autoprune.prune(doc, autoprune.used_paths(needed_by), days)
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
        resolved = load_and_resolve(Path(args.project))
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
    except OSError as exc:
        print(msg("cli.write_failed", path=output, exc=exc), file=sys.stderr)
        return 1
    print(msg("cli.import_done", output=output))
    for w in result.warnings:
        print(msg("cli.warning_prefix", w=w), file=sys.stderr)
    if result.raw_items:
        print(msg("cli.raw_items_notice", n=len(result.raw_items), output=output))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    output = Path(args.output) if args.output else yaml_path.with_suffix(".mp4")
    if args.fetch and not _run_fetch(yaml_path):
        return 1

    try:
        resolved = load_and_resolve(yaml_path)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    melt = probe.find_melt()
    if melt is None:
        print(msg("cli.render_missing_melt"), file=sys.stderr)
        return 1
    original = resolved
    resolved = _with_proxies(resolved, args.proxy)
    if resolved is None:
        return 1
    _record_use(yaml_path, original, resolved)
    if not args.no_prune:
        # (with --proxy the originals are kept too: recorded as used above)
        _autoprune(yaml_path, resolved)

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
                )
            except (KdenliveAsCodeError, OSError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(msg("cli.render_done", output=output, frames=resolved.total_frames, fps=resolved.fps.fps))
            if args.keep_kdenlive:
                print(msg("cli.render_kept_kdenlive", path=kdenlive_path))
            return 0
        result = subprocess.run(
            [melt, str(kdenlive_path), "-consumer", f"avformat:{output}"],
            capture_output=True,
            text=True,
            # C locale, or decimals like `gain: 2.5`/`speed: 1.08` get
            # truncated under a comma-decimal locale - see probe.melt_env
            env=probe.melt_env(),
        )
        if result.returncode != 0:
            print(msg("cli.render_failed", path=output, stderr=result.stderr.strip()), file=sys.stderr)
            return 1

    print(msg("cli.render_done", output=output, frames=resolved.total_frames, fps=resolved.fps.fps))
    if args.keep_kdenlive:
        print(msg("cli.render_kept_kdenlive", path=kdenlive_path))
    return 0


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
    p_build.add_argument("--pack-tracks", action="store_true", help=msg("cli.help.pack_tracks"))
    p_build.add_argument("--fetch", action="store_true", help=msg("cli.help.fetch_first"))
    p_build.add_argument("--proxy", metavar="WxH", help=msg("cli.help.proxy"))
    p_build.set_defaults(func=cmd_build)

    p_validate = sub.add_parser("validate", help=msg("cli.help.validate"))
    p_validate.add_argument("project", help=msg("cli.help.project_path"))
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
    p_render.add_argument("--no-prune", action="store_true", help=msg("cli.help.no_prune"))
    p_render.set_defaults(func=cmd_render)

    p_fetch = sub.add_parser("fetch", help=msg("cli.help.fetch"))
    p_fetch.add_argument("project", help=msg("cli.help.project_path"))
    p_fetch.add_argument("--verify", action="store_true", help=msg("cli.help.fetch_verify"))
    p_fetch.add_argument("--prune", action="store_true", help=msg("cli.help.fetch_prune"))
    p_fetch.set_defaults(func=cmd_fetch)

    p_upscaler = sub.add_parser("install-upscaler", help=msg("cli.help.install_upscaler"))
    p_upscaler.set_defaults(func=cmd_install_upscaler)

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
