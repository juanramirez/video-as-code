"""Shared fixtures: tiny assets generated with ffmpeg (no binaries in the
repo) and optional lookup of a real `melt` binary to validate the output
against the actual MLT engine."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


@pytest.fixture(scope="session")
def assets_dir(tmp_path_factory) -> Path:
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg is not available: can't generate the test assets")
    d = tmp_path_factory.mktemp("assets")
    common = ["-y", "-loglevel", "error"]
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3", "-pix_fmt", "yuv420p", str(d / "clip.mp4")],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "sine=frequency=440:duration=5", str(d / "tone.mp3")],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "color=c=red:size=320x240", "-frames:v", "1", str(d / "photo.png")],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "color=c=blue:size=320x240", "-frames:v", "1", str(d / "photo2.png")],
        check=True,
    )
    return d


def _find_melt() -> tuple[str, dict] | None:
    """Looks for a usable `melt`. In this dev environment, kdenlive ships as
    a snap and doesn't expose `melt` on PATH, so if needed we invoke the
    snap's internal binary with the environment variables it requires (see
    README)."""
    exe = shutil.which("melt") or shutil.which("melt-7")
    if exe:
        return exe, {}

    for base in sorted(Path("/snap/kdenlive").glob("*")):
        melt_bin = base / "usr" / "bin" / "melt-7"
        libdir = base / "usr" / "lib" / "x86_64-linux-gnu"
        if melt_bin.exists() and libdir.exists():
            env = {
                "LD_LIBRARY_PATH": str(libdir),
                "MLT_REPOSITORY": str(libdir / "mlt-7"),
                "MLT_DATA": str(base / "usr" / "share" / "mlt-7"),
            }
            return str(melt_bin), env
    return None


@pytest.fixture(scope="session")
def melt():
    found = _find_melt()
    if found is None:
        pytest.skip("no usable 'melt' (MLT engine) binary found on this system")
    exe, extra_env = found
    return exe, extra_env
