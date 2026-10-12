"""`derive:` bin clips (see models.DeriveSpec): files generated with
ffmpeg from another file instead of being stored.

Where they go: `<sources_dir>/.derived/<clip id>-<key>.<ext>`, where
`<key>` hashes the whole spec (input reference + every operation), so
editing the spec yields a new file name - and with it a fresh
generation - while an unchanged spec keeps pointing at the same file.
The extension follows the input's kind: a gif stays a gif, a still image
becomes a .png (lossless, whatever it was), audio becomes .m4a and
anything else (video) .mp4.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import probe
from .models import DeriveSpec

DERIVED_DIR = ".derived"


def spec_key(spec: DeriveSpec, input_ref: str) -> str:
    """Short stable hash of everything that determines the output.
    `input_ref` names the input the way the YAML does (for `clip:` inputs,
    the referenced clip's own output file name, so editing an upstream
    derive also renames - and regenerates - everything derived from it)."""
    canonical = {
        "input": input_ref,
        "trim": list(spec.trim) if spec.trim else None,
        "crop": list(spec.crop) if spec.crop else None,
        "scale": list(spec.scale) if spec.scale else None,
        "fps": spec.fps,
        "reverse": spec.reverse,
        "volume": spec.volume,
    }
    return hashlib.sha1(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:10]


def output_suffix(input_path: Path) -> str:
    if input_path.suffix.lower() == ".gif":
        return ".gif"
    kind = probe.guess_type(input_path)
    if kind == "image":
        return ".png"
    if kind == "audio":
        return ".m4a"
    return ".mp4"


def output_path(clip_id: str, spec: DeriveSpec, input_path: Path, input_ref: str, derived_dir: Path) -> Path:
    return derived_dir / f"{clip_id}-{spec_key(spec, input_ref)}{output_suffix(input_path)}"
