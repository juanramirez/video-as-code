from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from . import probe, reverse, xmlgen
from .errors import KdgenError
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
    except KdgenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    xmlgen.write(resolved, output)
    print(msg("cli.build_done", output=output, frames=resolved.total_frames, fps=resolved.fps.fps))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        resolved = load_and_resolve(Path(args.project))
    except KdgenError as exc:
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
    except (KdgenError, ValueError) as exc:
        print(msg("cli.import_error", path=kdenlive_path, exc=exc), file=sys.stderr)
        return 1

    output.write_text(_dump_yaml(result.data), encoding="utf-8")
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
    except KdgenError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    melt = probe.find_melt()
    if melt is None:
        print(msg("cli.render_missing_melt"), file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        kdenlive_path = Path(tmp_dir) / (yaml_path.stem + ".kdenlive")
        xmlgen.write(resolved, kdenlive_path)
        result = subprocess.run(
            [melt, str(kdenlive_path), "-consumer", f"avformat:{output}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(msg("cli.render_failed", path=output, stderr=result.stderr.strip()), file=sys.stderr)
            return 1

    print(msg("cli.render_done", output=output, frames=resolved.total_frames, fps=resolved.fps.fps))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kdgen", description=msg("cli.help.description"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help=msg("cli.help.build"))
    p_build.add_argument("project", help=msg("cli.help.project_path"))
    p_build.add_argument("-o", "--output", help=msg("cli.help.build_output"))
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
    p_render.set_defaults(func=cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
