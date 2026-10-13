"""upscale.py and derive:'s `upscale:`, with a stand-in for
realesrgan-ncnn-vulkan (VIDEO_AS_CODE_REALESRGAN) that enlarges x4 with
ffmpeg and logs each call - the real one is far too slow for tests."""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from video_as_code import cli, derive, upscale  # noqa: E402
from video_as_code.errors import ValidationErrors  # noqa: E402
from video_as_code.models import DeriveSpec  # noqa: E402
from video_as_code.schema import load_and_resolve, load_yaml  # noqa: E402

from test_derive import _bin, _project  # noqa: E402


@pytest.fixture
def fake_esrgan(tmp_path: Path, monkeypatch) -> Path:
    """Installs the stand-in; returns its call log (one line per frame)."""
    log = tmp_path / "esrgan_calls.log"
    script = tmp_path / "fake-esrgan" / "realesrgan-ncnn-vulkan"
    script.parent.mkdir()
    script.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys\n"
        "a = sys.argv[1:]\n"
        "src, dst, scale = a[a.index('-i') + 1], a[a.index('-o') + 1], a[a.index('-s') + 1]\n"
        f"open({str(log)!r}, 'a').write(src + '\\n')\n"
        "subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-vf', f'scale=iw*{scale}:ih*{scale}', dst], check=True)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("VIDEO_AS_CODE_REALESRGAN", str(script))
    return log


def _gif(path: Path, colors: list[tuple[int, int, int]], delays: list[int]) -> Path:
    frames = [Image.new("RGB", (20, 16), c) for c in colors]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=delays, loop=0, disposal=2)
    return path


def _delays(path: Path) -> list[int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "frame=duration_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.split()
    return [round(float(d) * 1000) for d in out]


def test_gif_is_upscaled_frame_by_frame_keeping_its_delays(tmp_path: Path, fake_esrgan: Path):
    red, blue = (200, 0, 0), (0, 0, 200)
    src = _gif(tmp_path / "anim.gif", [red, blue, red, blue], [250, 250, 250, 1000])
    out = tmp_path / "big.gif"
    upscale.upscale("c", src, out)
    with Image.open(out) as im:
        assert im.size == (80, 64)
        assert im.n_frames == 4
    assert _delays(out) == [250, 250, 250, 1000]
    assert len(fake_esrgan.read_text().splitlines()) == 2  # two distinct frames, each upscaled once


def test_still_becomes_a_png_four_times_larger(tmp_path: Path, fake_esrgan: Path):
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (30, 20), (10, 120, 30)).save(src)
    project = _project(tmp_path, [f"{{id: big, derive: {{path: {src}, upscale: 4}}}}"])
    clip = _bin(project)["big"]
    load_and_resolve(project)
    assert clip.path.suffix == ".png"
    with Image.open(clip.path) as im:
        assert im.size == (120, 80)


def test_the_other_operations_run_first_on_the_small_picture(tmp_path: Path, fake_esrgan: Path):
    src = tmp_path / "photo.png"
    Image.new("RGB", (30, 20), (10, 120, 30)).save(src)
    project = _project(tmp_path, [f"{{id: big, derive: {{path: {src}, crop: {{x: 0, y: 0, w: 10, h: 10}}, upscale: 4}}}}"])
    load_and_resolve(project)
    with Image.open(_bin(project)["big"].path) as im:
        assert im.size == (40, 40)


def test_upscale_must_be_4(tmp_path: Path):
    with pytest.raises(ValidationErrors, match="upscale"):
        load_yaml(_project(tmp_path, ["{id: big, derive: {path: x.png, upscale: 2}}"]))


def test_adding_upscale_keeps_older_specs_keys():
    """A spec without `upscale` hashes exactly as before it existed, so
    already generated files aren't regenerated; with it, differently."""
    import hashlib
    import json

    legacy = {"input": "x.mp4", "trim": [0.0, 1.0], "crop": None, "scale": None, "fps": None, "reverse": False, "volume": None}
    legacy_key = hashlib.sha1(json.dumps(legacy, sort_keys=True).encode()).hexdigest()[:10]
    plain = DeriveSpec(path=Path("x.mp4"), trim=(0.0, 1.0))
    assert derive.spec_key(plain, "x.mp4") == legacy_key
    assert derive.spec_key(DeriveSpec(path=Path("x.mp4"), trim=(0.0, 1.0), upscale=4), "x.mp4") != legacy_key


def test_missing_upscaler_points_at_installing_it(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(upscale, "find_binary", lambda: None)
    src = tmp_path / "photo.png"
    Image.new("RGB", (8, 8)).save(src)
    with pytest.raises(ValidationErrors, match="install-upscaler"):
        load_and_resolve(_project(tmp_path, [f"{{id: big, derive: {{path: {src}, upscale: 4}}}}"]))


def test_install_refuses_a_download_with_the_wrong_checksum(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(upscale, "can_install", lambda: True)
    monkeypatch.setattr(upscale.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"not the release"))
    with pytest.raises(upscale.UpscaleError, match="sha256"):
        upscale.install()
    assert not upscale.install_dir().exists()


def test_fetch_offers_to_install_it_and_respects_a_no(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(upscale, "find_binary", lambda: None)
    monkeypatch.setattr(upscale, "can_install", lambda: True)
    installs = []
    monkeypatch.setattr(upscale, "install", lambda report: installs.append(1))
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")
    project = _project(tmp_path, ["{id: big, derive: {path: x.png, upscale: 4}}"])
    assert cli._offer_upscaler(project)
    assert len(asked) == 1 and not installs
