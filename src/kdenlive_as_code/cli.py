from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from . import i18n, probe, reverse, trackpack, xmlgen
from .errors import KdenliveAsCodeError
from .i18n import msg
from .schema import load_and_resolve


def _yaml_str_representer(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _dump_yaml(data: dict) -> str:
    dumper = yaml.Dumper
    dumper.add_representer(str, _yaml_str_representer)
    return yaml.dump(data, Dumper=dumper, sort_keys=False, allow_unicode=True, default_flow_style=False)


def cmd_build(args: argparse.Namespace) -> int:
    yaml_path = Path(args.project)
    output = Path(args.output) if args.output else yaml_path.with_suffix(".kdenlive")
    try:
        resolved = load_and_resolve(yaml_path)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
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


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        resolved = load_and_resolve(Path(args.project))
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
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

    try:
        resolved = load_and_resolve(yaml_path)
    except KdenliveAsCodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    melt = probe.find_melt()
    if melt is None:
        print(msg("cli.render_missing_melt"), file=sys.stderr)
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
        result = subprocess.run(
            [melt, str(kdenlive_path), "-consumer", f"avformat:{output}"],
            capture_output=True,
            text=True,
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
    parser = argparse.ArgumentParser(prog="kdenlive-as-code", description=msg("cli.help.description"))
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
    p_render.set_defaults(func=cmd_render)

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
