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


_LOCAL_MLT_BUILD = Path.home() / "dev" / "mlt" / "install"


def _find_melt() -> tuple[str, dict] | None:
    """Looks for a usable `melt`. Prefers a from-source build at
    ~/dev/mlt/install if present: on this machine, the system package
    (apt's `melt`) and kdenlive's own snap are both missing a working
    `qtblend` (the snap's is linked against a newer Qt6 than the system
    has; apt's build doesn't compile the Qt module in at all), and
    `qtblend` is exactly what multi-track compositing (`transition:
    dissolve` in scenes.py, the default per-clip position/size filter)
    needs - so most of the suite can get by without it, but anything that
    actually exercises track blending can't be verified without it. Falls
    back to PATH, then the snap's raw binary (see README), for
    environments without that local build."""
    local_bin = _LOCAL_MLT_BUILD / "bin" / "melt-7"
    local_libdir = _LOCAL_MLT_BUILD / "lib"
    if local_bin.exists() and local_libdir.exists():
        env = {
            "LD_LIBRARY_PATH": str(local_libdir),
            "MLT_REPOSITORY": str(local_libdir / "mlt-7"),
        }
        data_dir = _LOCAL_MLT_BUILD / "share" / "mlt-7"
        if data_dir.exists():
            env["MLT_DATA"] = str(data_dir)
        return str(local_bin), env

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


@pytest.fixture(scope="session")
def melt_with_qtblend(melt):
    """Like `melt`, but skips the test if this `melt` can't actually load
    `qtblend` - needed by anything that checks real cross-track/cross-clip
    compositing (not just that the XML looks plausible), which most of the
    suite avoids needing (see `_find_melt`'s docstring) but a few tests
    (multi-track dissolve) fundamentally can't route around."""
    exe, extra_env = melt
    import os

    result = subprocess.run(
        [exe, "-query", "transitions"],
        capture_output=True,
        text=True,
        env={**os.environ, **extra_env},
        timeout=30,
    )
    if "qtblend" not in result.stdout:
        pytest.skip("this 'melt' can't load 'qtblend': can't verify real cross-track compositing")
    return exe, extra_env
