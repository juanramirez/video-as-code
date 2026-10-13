"""`derive:`'s `upscale: 4` (see models.DeriveSpec): enlarging a still
image or a gif with Real-ESRGAN, an AI upscaler that invents plausible
detail instead of just stretching pixels - for low-resolution album items
(Google Photos' own animations are ~800x600) that would look blurry at
1920x1080.

It runs the external `realesrgan-ncnn-vulkan` (the realesrgan-x4plus
model, so x4 only) on any Vulkan device, an integrated GPU included -
slowly there: a minute or two PER FRAME. Hence stills and gifs only (a
video would take hours), and a gif's repeated frames (Google's
animations often hold one by repeating it) are upscaled just once.

A gif is taken apart with ffmpeg (each frame as composited, at its own
delay, read with ffprobe) and put back together with Pillow: ffmpeg
itself can't (its image inputs snap delays to 1/25 s steps), while
Pillow writes the original per-frame delays exactly.

Where the program is looked for: VIDEO_AS_CODE_REALESRGAN (path to the
binary, its `models/` next to it), else what `install` puts in
<XDG data dir>/video-as-code/realesrgan/, else `realesrgan-ncnn-vulkan`
on the PATH. `install` downloads a pinned release and checks its sha256
before unpacking it.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .errors import KdenliveAsCodeError
from .i18n import msg

FACTOR = 4
_MODEL = "realesrgan-x4plus"
_BINARY = "realesrgan-ncnn-vulkan"
_ENV = "VIDEO_AS_CODE_REALESRGAN"
# Real-ESRGAN v0.2.5.0's Linux build (the last release with ncnn-vulkan
# binaries); its sha256 as downloaded on 2026-10-13
_RELEASE_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip"
_RELEASE_SHA256 = "e5aa6eb131234b87c0c51f82b89390f5e3e642b7b70f2b9bbe95b6a285a40c96"
RELEASE_SIZE_MB = 47
_TIMEOUT_PER_FRAME_S = 1800


class UpscaleError(KdenliveAsCodeError):
    pass


def install_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "video-as-code" / "realesrgan"


def find_binary() -> Optional[Path]:
    if os.environ.get(_ENV):
        return Path(os.environ[_ENV]).expanduser()
    installed = install_dir() / _BINARY
    if installed.is_file():
        return installed
    on_path = shutil.which(_BINARY)
    return Path(on_path) if on_path else None


def can_install() -> bool:
    """Whether `install` has a build for this machine (Linux x86-64 only)."""
    return platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64")


def install(report: Callable[[str], None] = lambda text: None) -> Path:
    """Downloads and unpacks the pinned release into install_dir();
    returns the binary's path. Raises UpscaleError."""
    if not can_install():
        raise UpscaleError(msg("upscale.no_build", system=f"{platform.system()} {platform.machine()}"))
    target = install_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        archive = Path(tmp) / "realesrgan.zip"
        report(msg("upscale.downloading", url=_RELEASE_URL))
        try:
            with urllib.request.urlopen(_RELEASE_URL, timeout=60) as response, archive.open("wb") as fh:
                shutil.copyfileobj(response, fh)
        except OSError as exc:
            raise UpscaleError(msg("upscale.download_failed", exc=exc)) from exc
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != _RELEASE_SHA256:
            raise UpscaleError(msg("upscale.bad_checksum", got=digest, expected=_RELEASE_SHA256))
        unpacked = Path(tmp) / "unpacked"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpacked)
        binary = next(unpacked.rglob(_BINARY), None)
        if binary is None:
            raise UpscaleError(msg("upscale.bad_archive"))
        binary.chmod(0o755)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(binary.parent), str(target))
    report(msg("upscale.installed", path=target / _BINARY))
    return target / _BINARY


def _frame_delays_ms(path: Path) -> list[int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "frame=duration_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120,
    ).stdout.split()
    return [round(float(d) * 1000) for d in out if d not in ("", "N/A")]


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def upscale(
    clip_id: str,
    input_path: Path,
    output: Path,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Writes `input_path` enlarged x4 to `output` (.png for a still, .gif
    for a gif). Raises UpscaleError."""
    report = on_progress or (lambda text: None)
    binary = find_binary()
    if binary is None or not binary.is_file():
        raise UpscaleError(msg("upscale.missing", id=clip_id, env=_ENV))
    is_gif = output.suffix.lower() == ".gif"
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        frames_in = Path(tmp) / "in"
        frames_out = Path(tmp) / "out"
        frames_in.mkdir()
        frames_out.mkdir()
        # every frame as composited (a gif frame may only hold what changed)
        extract = ["ffmpeg", "-v", "error", "-i", str(input_path), "-fps_mode", "passthrough"]
        if not is_gif:
            extract += ["-frames:v", "1"]
        subprocess.run(extract + [str(frames_in / "%05d.png")], check=True, capture_output=True, timeout=600)
        frames = sorted(frames_in.iterdir())
        # identical frames are upscaled once (the slow part)
        unique: dict[str, Path] = {}
        for frame in frames:
            unique.setdefault(_sha1(frame), frame)
        for n, (digest, frame) in enumerate(unique.items(), start=1):
            report(msg("upscale.frame", id=clip_id, n=n, total=len(unique)))
            run = subprocess.run(
                [str(binary), "-i", str(frame), "-o", str(frames_out / f"{digest}.png"),
                 "-n", _MODEL, "-s", str(FACTOR), "-m", str(binary.parent / "models")],
                capture_output=True, text=True, timeout=_TIMEOUT_PER_FRAME_S,
            )
            if run.returncode != 0 or not (frames_out / f"{digest}.png").is_file():
                detail = (run.stderr.strip().splitlines() or ["?"])[-1]
                raise UpscaleError(msg("upscale.failed", id=clip_id, detail=detail))
        upscaled = [frames_out / f"{_sha1(frame)}.png" for frame in frames]
        if not is_gif:
            shutil.copyfile(upscaled[0], output)
            return
        try:
            from PIL import Image
        except ImportError as exc:
            raise UpscaleError(msg("upscale.needs_pillow", id=clip_id)) from exc
        delays = _frame_delays_ms(input_path)
        if len(delays) != len(frames):
            delays = [100] * len(frames)  # (no usable timing: gif's own default)
        images = [Image.open(p).convert("RGB") for p in upscaled]
        images[0].save(output, save_all=True, append_images=images[1:], duration=delays, loop=0, disposal=2)
