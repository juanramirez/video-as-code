"""Shared fixtures: tiny assets generated with ffmpeg (no binaries in the
repo) and optional lookup of a real `melt` binary to validate the output
against the actual MLT engine."""
from __future__ import annotations

import os
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
    # really moving footage at boomerang_trim_hold's thumbnail scale (testsrc's clip.mp4 isn't: only
    # its small seconds counter changes, which a 32x18 thumbnail averages away)
    subprocess.run(
        ["ffmpeg", *common, "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3", "-pix_fmt", "yuv420p", str(d / "moving.mp4")],
        check=True,
    )
    # 1.0s gif whose LAST picture (green) is held for 0.6s, after two 0.2s ones (red, blue) - for
    # boomerang_trim_hold (see ClipPlacementSpec.boomerang_trim_hold)
    subprocess.run(
        [
            "ffmpeg", *common,
            "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=10:duration=0.2",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=0.2",
            "-f", "lavfi", "-i", "color=c=green:size=320x240:rate=10:duration=0.6",
            "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", str(d / "held.gif"),
        ],
        check=True,
    )
    # 3s video WITH sound (the other clips are silent) - for derive:'s audio handling
    subprocess.run(
        [
            "ffmpeg", *common,
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-pix_fmt", "yuv420p", "-shortest", str(d / "av.mp4"),
        ],
        check=True,
    )
    return d


@pytest.fixture(scope="session")
def _openshot_dir(tmp_path_factory) -> Path:
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg is not available: can't generate the test assets")
    d = tmp_path_factory.mktemp("openshot_transitions")
    (d / "common").mkdir()
    (d / "extra").mkdir()
    # laid out as openshot-qt's: grayscale JPGs (and SVGs) under common/ and
    # extra/. A square map, dark on the left: a 4:3 frame shows it whole
    # only if it's stretched to the frame, as OpenShot and lumas.py do.
    for folder, name, expr in (("extra", "ramp_left", "X/W*255"), ("common", "ramp_top", "Y/H*255")):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:size=120x120",
             "-vf", f"geq=lum='{expr}':cb=128:cr=128,format=gray", "-frames:v", "1", str(d / folder / f"{name}.jpg")],
            check=True,
        )
    return d


@pytest.fixture
def openshot_transitions(_openshot_dir, tmp_path, monkeypatch) -> Path:
    """A stand-in for openshot-qt's transitions folder (lumas.py), with a
    cache folder of this test's own for the PGMs made from it."""
    monkeypatch.setenv("VIDEO_AS_CODE_OPENSHOT_TRANSITIONS", str(_openshot_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return _openshot_dir


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
            # same reason as probe.melt_env: decimals get truncated under a
            # comma-decimal locale
            "LC_ALL": "C",
        }
        data_dir = _LOCAL_MLT_BUILD / "share" / "mlt-7"
        if data_dir.exists():
            env["MLT_DATA"] = str(data_dir)
        # A from-source MLT only looks for frei0r plugins in its own default
        # dirs (e.g. /usr/lib/frei0r-1), not in Debian/Ubuntu's multiarch
        # /usr/lib/<triplet>/frei0r-1 - so without this, every frei0r-backed
        # transition/filter (iris, flip, glitch, cairogradient, delaygrab,
        # chroma key) silently renders as a no-op and those tests fail.
        # A FREI0R_PATH already set by the caller wins.
        if "FREI0R_PATH" not in os.environ:
            frei0r_dirs = sorted(Path("/usr/lib").glob("*/frei0r-1"))
            if frei0r_dirs:
                env["FREI0R_PATH"] = ":".join(str(d) for d in frei0r_dirs)
        return str(local_bin), env

    exe = shutil.which("melt") or shutil.which("melt-7")
    if exe:
        return exe, {"LC_ALL": "C"}

    for base in sorted(Path("/snap/kdenlive").glob("*")):
        melt_bin = base / "usr" / "bin" / "melt-7"
        libdir = base / "usr" / "lib" / "x86_64-linux-gnu"
        if melt_bin.exists() and libdir.exists():
            env = {
                "LD_LIBRARY_PATH": str(libdir),
                "MLT_REPOSITORY": str(libdir / "mlt-7"),
                "MLT_DATA": str(base / "usr" / "share" / "mlt-7"),
                "LC_ALL": "C",
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
