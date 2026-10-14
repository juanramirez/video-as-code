"""`derive:` bin clips (models.DeriveSpec, derive.py)."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from video_as_code import derive
from video_as_code.models import DeriveSpec
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve, load_yaml

ALBUM = "{album: 'https://photos.app.goo.gl/x'}"


def _project(tmp_path: Path, bin_entries: list[str], sources: str = ALBUM, clips: str = "") -> Path:
    bin_lines = "\n".join(f"  - {entry}" for entry in bin_entries)
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240, sources: {sources}}}
bin:
{bin_lines}
timeline:
  tracks:
    - type: video
      clips: [{clips}]
""",
        encoding="utf-8",
    )
    return path


def _bin(path: Path) -> dict:
    return {c.id: c for c in load_yaml(path).bin}


# --- parsing ----------------------------------------------------------------


def test_derive_output_goes_to_the_derived_cache(tmp_path: Path):
    clips = _bin(_project(tmp_path, ["{id: cut, derive: {source: VID_1.mp4, trim: [1, 2]}}"]))
    spec, path = clips["cut"].derive, clips["cut"].path
    assert (spec.source, spec.trim) == ("VID_1.mp4", (1.0, 2.0))
    assert path.parent == tmp_path / "assets" / ".derived"
    assert path.name.startswith("cut-") and path.suffix == ".mp4"


@pytest.mark.parametrize(
    "derive, suffix",
    [
        ("{path: a.gif, reverse: true}", ".gif"),
        ("{path: a.jpg, crop: {x: 0, y: 0, w: 10, h: 10}}", ".png"),
        ("{path: a.mp3, volume: 2}", ".m4a"),
        ("{path: a.mov, fps: 30}", ".mp4"),
    ],
)
def test_derive_output_kind_follows_the_input(tmp_path: Path, derive: str, suffix: str):
    assert _bin(_project(tmp_path, [f"{{id: d, derive: {derive}}}"]))["d"].path.suffix == suffix


def test_derive_spec_change_changes_the_output_name(tmp_path: Path):
    first = _bin(_project(tmp_path, ["{id: d, derive: {path: a.mp4, trim: [1, 2]}}"]))["d"].path
    same = _bin(_project(tmp_path, ["{id: d, derive: {path: a.mp4, trim: [1, 2]}}"]))["d"].path
    other = _bin(_project(tmp_path, ["{id: d, derive: {path: a.mp4, trim: [1, 3]}}"]))["d"].path
    assert first == same and first != other


def test_derive_from_another_clip_follows_it(tmp_path: Path):
    entries = [
        "{id: loud, derive: {clip: cut, volume: 2}}",  # declared before its input
        "{id: cut, derive: {path: a.mp4, trim: [1, 2]}}",
    ]
    clips = _bin(_project(tmp_path, entries))
    assert clips["loud"].derive.clip == "cut"
    # editing the upstream derive renames (and so regenerates) the downstream one too
    entries[1] = "{id: cut, derive: {path: a.mp4, trim: [1, 3]}}"
    assert _bin(_project(tmp_path, entries))["loud"].path != clips["loud"].path


@pytest.mark.parametrize(
    "entries, needle",
    [
        (["{id: d, path: a.mp4, derive: {path: a.mp4, fps: 30}}"], "derive"),
        (["{id: d, derive: [a.mp4]}"], "derive"),
        (["{id: d, derive: {path: a.mp4, fps: 30, speed: 2}}"], "speed"),
        (["{id: d, derive: {fps: 30}}"], "exactly one input"),
        (["{id: d, derive: {path: a.mp4, clip: x, fps: 30}}"], "exactly one input"),
        (["{id: d, derive: {path: a.mp4, trim: [3, 2]}}"], "derive.trim"),
        (["{id: d, derive: {path: a.mp4, trim: 3}}"], "derive.trim"),
        (["{id: d, derive: {path: a.mp4, crop: {x: 0, y: 0, w: 0, h: 10}}}"], "derive.crop"),
        (["{id: d, derive: {path: a.mp4, scale: [-1, -1]}}"], "derive.scale"),
        (["{id: d, derive: {path: a.mp4, fps: 0}}"], "derive.fps"),
        (["{id: d, derive: {path: a.mp4, reverse: maybe}}"], "derive.reverse"),
        (["{id: d, derive: {path: a.mp4, intra: 1}}"], "derive.intra"),
        (["{id: d, derive: {path: a.mp4, volume: -1}}"], "derive.volume"),
        (["{id: d, derive: {path: a.mp4, devignette: 1}}"], "derive.devignette"),
        (["{id: d, derive: {path: a.mp4, devignette: 0}}"], "derive.devignette"),
        (["{id: d, derive: {path: a.mp4, devignette: true}}"], "derive.devignette"),
        (["{id: d, derive: {path: a.mp4, cut: [[2, 1]]}}"], "derive.cut"),
        (["{id: d, derive: {path: a.mp4, cut: [[3, 4], [1, 2]]}}"], "derive.cut"),
        (["{id: d, derive: {path: a.mp4, cut: [1, 2]}}"], "derive.cut"),
        (["{id: d, derive: {path: a.mp4}}"], "no operation"),
        (["{id: d, derive: {clip: nope, fps: 30}}"], "nope"),
        (["{id: t, type: title, text: Hi}", "{id: d, derive: {clip: t, fps: 30}}"], "title"),
        (["{id: a, derive: {clip: b, fps: 30}}", "{id: b, derive: {clip: a, fps: 30}}"], "cycle"),
        (["{id: d, derive: {source: ../x.mp4, fps: 30}}"], "../x.mp4"),
    ],
)
def test_derive_invalid(tmp_path: Path, entries: list[str], needle: str):
    with pytest.raises(ValidationErrors) as exc:
        load_yaml(_project(tmp_path, entries))
    assert needle in str(exc.value)


def test_derive_source_needs_an_album(tmp_path: Path):
    with pytest.raises(ValidationErrors) as exc:
        load_yaml(_project(tmp_path, ["{id: d, derive: {source: VID_1.mp4, fps: 30}}"], sources="{}"))
    assert "sources.album" in str(exc.value)


# --- generation -------------------------------------------------------------


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _first_pixel(path: Path) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return raw[0], raw[1], raw[2]


@pytest.fixture
def ffmpeg_calls(monkeypatch):
    """Counts derive.generate runs (i.e. real ffmpeg invocations)."""
    calls = []
    real = derive.generate

    def counting(clip_id, *args):
        calls.append(clip_id)
        return real(clip_id, *args)

    monkeypatch.setattr(derive, "generate", counting)
    return calls


def test_derive_trim_keeps_sound_and_is_cached(tmp_path: Path, assets_dir: Path, ffmpeg_calls):
    project = _project(tmp_path, [f"{{id: cut, derive: {{path: {assets_dir / 'av.mp4'}, trim: [0.5, 2.0]}}}}"])
    load_and_resolve(project)
    info = _probe(_bin(project)["cut"].path)
    assert float(info["format"]["duration"]) == pytest.approx(1.5, abs=0.1)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert ffmpeg_calls == ["cut"]
    load_and_resolve(project)  # unchanged spec and input: nothing regenerated
    assert ffmpeg_calls == ["cut"]


def test_derive_spec_change_regenerates_and_drops_the_old_file(tmp_path: Path, assets_dir: Path, ffmpeg_calls):
    entry = "{{id: cut, derive: {{path: {p}, trim: [0, {end}]}}}}"
    first = _project(tmp_path, [entry.format(p=assets_dir / "av.mp4", end=1)])
    load_and_resolve(first)
    old = _bin(first)["cut"].path
    second = _project(tmp_path, [entry.format(p=assets_dir / "av.mp4", end=2)])
    load_and_resolve(second)
    new = _bin(second)["cut"].path
    assert ffmpeg_calls == ["cut", "cut"]
    assert new.exists() and not old.exists()
    assert float(_probe(new)["format"]["duration"]) == pytest.approx(2.0, abs=0.1)


def test_derive_input_change_regenerates_but_an_identical_copy_doesnt(tmp_path: Path, assets_dir: Path, ffmpeg_calls):
    src = tmp_path / "in.mp4"
    shutil.copy(assets_dir / "av.mp4", src)
    project = _project(tmp_path, ["{id: cut, derive: {path: in.mp4, trim: [0, 1]}}"])
    load_and_resolve(project)
    shutil.copy(assets_dir / "av.mp4", src)  # same bytes, new mtime (like a re-download)
    os.utime(src, ns=(1, 1))
    load_and_resolve(project)
    assert ffmpeg_calls == ["cut"]
    shutil.copy(assets_dir / "moving.mp4", src)  # different content
    load_and_resolve(project)
    assert ffmpeg_calls == ["cut", "cut"]


def test_derive_reverse_gif(tmp_path: Path, assets_dir: Path):
    # held.gif goes red -> blue -> green: reversed, it starts green
    project = _project(tmp_path, [f"{{id: back, derive: {{path: {assets_dir / 'held.gif'}, reverse: true}}}}"])
    load_and_resolve(project)
    out = _bin(project)["back"].path
    assert out.suffix == ".gif"
    r, g, b = _first_pixel(out)
    assert g > 100 and r < 80 and b < 80
    assert _first_pixel(assets_dir / "held.gif")[0] > 150  # (and the original starts red)


def test_derive_cut_removes_a_range_from_picture_and_sound(tmp_path: Path, assets_dir: Path):
    total = float(_probe(assets_dir / "av.mp4")["format"]["duration"])
    project = _project(tmp_path, [f"{{id: short, derive: {{path: {assets_dir / 'av.mp4'}, cut: [[0.5, 1.0]]}}}}"])
    load_and_resolve(project)
    info = _probe(_bin(project)["short"].path)
    assert float(info["format"]["duration"]) == pytest.approx(total - 0.5, abs=0.1)
    for stream in info["streams"]:  # picture and sound shortened alike, still in step
        assert float(stream["duration"]) == pytest.approx(total - 0.5, abs=0.1)


def test_derive_cut_and_trim_both_count_in_the_inputs_seconds(tmp_path: Path, assets_dir: Path):
    project = _project(
        tmp_path, [f"{{id: short, derive: {{path: {assets_dir / 'av.mp4'}, trim: [0.25, 2.0], cut: [[0.5, 1.0]]}}}}"]
    )
    load_and_resolve(project)
    assert float(_probe(_bin(project)["short"].path)["format"]["duration"]) == pytest.approx(1.25, abs=0.1)


def test_derive_crop_scale_fps_on_a_silent_video(tmp_path: Path, assets_dir: Path):
    crop = "{x: 10, y: 10, w: 200, h: 100}"
    project = _project(
        tmp_path,
        [f"{{id: small, derive: {{path: {assets_dir / 'clip.mp4'}, crop: {crop}, scale: [100, -1], fps: 10}}}}"],
    )
    load_and_resolve(project)
    info = _probe(_bin(project)["small"].path)
    (video,) = info["streams"]  # no audio stream invented for a silent input
    assert (video["width"], video["height"]) == (100, 50)
    assert video["r_frame_rate"] == "10/1"


def test_derive_intra_makes_every_frame_a_keyframe(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, [f"{{id: still, derive: {{path: {assets_dir / 'av.mp4'}, trim: [0, 1], intra: true}}}}"])
    load_and_resolve(project)
    flags = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "frame=key_frame", "-of", "csv=p=0",
         str(_bin(project)["still"].path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert len(flags) > 1 and {f.strip(",") for f in flags} == {"1"}


def test_derive_devignette_evens_out_a_baked_in_vignette(tmp_path: Path):
    # a flat grey frame darkened by ffmpeg's own (forward) vignette, corners
    # at 0.81 of the centre: undoing it with the same figure gives back a
    # flat frame
    angle = math.acos(0.81 ** 0.25)
    dark = tmp_path / "dark.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0xa0a0a0:s=160x120",
         "-vf", f"format=rgb24,vignette=angle={angle}:dither=0", "-frames:v", "1", str(dark)],
        check=True,
    )
    project = _project(tmp_path, [f"{{id: flat, derive: {{path: {dark}, devignette: 0.81}}}}"])
    load_and_resolve(project)
    out = _bin(project)["flat"].path
    assert out.suffix == ".png"
    pixels = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    corner, centre = pixels[0], pixels[60 * 160 + 80]
    assert abs(corner - centre) <= 3


def test_derive_volume_on_audio(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, [f"{{id: quiet, derive: {{path: {assets_dir / 'tone.mp3'}, volume: 0.5}}}}"])
    load_and_resolve(project)
    out = _bin(project)["quiet"].path
    assert out.suffix == ".m4a"
    assert float(_probe(out)["format"]["duration"]) == pytest.approx(5.0, abs=0.1)


def test_derive_chain_generates_inputs_first(tmp_path: Path, assets_dir: Path, ffmpeg_calls):
    project = _project(
        tmp_path,
        [
            "{id: loud, derive: {clip: cut, volume: 2}}",
            f"{{id: cut, derive: {{path: {assets_dir / 'av.mp4'}, trim: [0, 1]}}}}",
        ],
    )
    load_and_resolve(project)
    assert ffmpeg_calls == ["cut", "loud"]
    assert float(_probe(_bin(project)["loud"].path)["format"]["duration"]) == pytest.approx(1.0, abs=0.1)


def test_derive_source_not_fetched_points_at_fetch(tmp_path: Path):
    project = _project(tmp_path, ["{id: cut, derive: {source: VID_1.mp4, trim: [0, 1]}}"])
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(project)
    assert "fetch" in str(exc.value)


def test_derive_missing_local_input(tmp_path: Path):
    project = _project(tmp_path, ["{id: cut, derive: {path: nope.mp4, trim: [0, 1]}}"])
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(project)
    assert "nope.mp4" in str(exc.value)


def test_derived_clip_is_usable_on_the_timeline(tmp_path: Path, assets_dir: Path):
    project = _project(
        tmp_path,
        [f"{{id: cut, derive: {{path: {assets_dir / 'av.mp4'}, trim: [0, 2]}}}}"],
        clips="{clip: cut, start: 0}",
    )
    (placement,) = load_and_resolve(project).tracks[0].placements
    assert placement.out_frame - placement.in_frame + 1 == pytest.approx(50, abs=1)  # 2s at 25fps


def _motion_photo(path: Path, video: Path, with_xmp: bool) -> Path:
    """A JPEG with `video` appended, as a phone's motion photo is - with
    or without the XMP that says where the video starts."""
    import io
    import struct

    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 20, 30)).save(buf, "JPEG")
    jpeg = buf.getvalue()
    tail = video.read_bytes()
    if with_xmp:
        xmp = (
            b'http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><rdf:li><Container:Item Item:Mime="video/mp4"'
            b' Item:Semantic="MotionPhoto" Item:Length="' + str(len(tail)).encode() + b'"/></rdf:li></x:xmpmeta>'
        )
        jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp + jpeg[2:]
    path.write_bytes(jpeg + tail)
    return path


@pytest.mark.parametrize("with_xmp", [True, False])
def test_derive_motion_takes_a_motion_photos_video(tmp_path: Path, assets_dir: Path, with_xmp: bool):
    photo = _motion_photo(tmp_path / "PXL_1.MP.jpg", assets_dir / "av.mp4", with_xmp)
    project = _project(tmp_path, [f"{{id: moving, derive: {{path: {photo}, motion: true, fps: 10}}}}"])
    load_and_resolve(project)
    clip = _bin(project)["moving"]
    assert clip.path.suffix == ".mp4"
    info = _probe(clip.path)
    assert float(info["format"]["duration"]) == pytest.approx(float(_probe(assets_dir / "av.mp4")["format"]["duration"]), abs=0.15)
    assert any(s["codec_type"] == "video" and s["r_frame_rate"] == "10/1" for s in info["streams"])


def test_derive_motion_on_a_plain_photo_says_so(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    photo = tmp_path / "plain.jpg"
    Image.new("RGB", (32, 24)).save(photo)
    with pytest.raises(ValidationErrors, match="motion photo"):
        load_and_resolve(_project(tmp_path, [f"{{id: moving, derive: {{path: {photo}, motion: true}}}}"]))


def test_derive_scaling_an_untagged_hd_video_down_converts_its_colours(tmp_path: Path):
    # MLT guesses an untagged video's matrix by height (BT.709 from 720 lines up, BT.601 below): a
    # small copy of an untagged HD video gets its colours converted, so it plays as the original did
    hd = tmp_path / "hd.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=1",
         "-pix_fmt", "yuv420p", str(hd)],
        check=True,
    )
    assert derive.video_info(hd) == (1280, 720, False)
    small = derive.build_command(DeriveSpec(path=hd, scale=(480, 270)), hd, tmp_path / "s.mp4", False, (1280, 720, False))
    assert "scale=in_color_matrix=bt709:out_color_matrix=bt601" in small[small.index("-vf") + 1]
    same = derive.build_command(DeriveSpec(path=hd, scale=(1920, 1080)), hd, tmp_path / "b.mp4", False, (1280, 720, False))
    assert "color_matrix" not in same[same.index("-vf") + 1]
    tagged = derive.build_command(DeriveSpec(path=hd, scale=(480, 270)), hd, tmp_path / "t.mp4", False, (1280, 720, True))
    assert "color_matrix" not in tagged[tagged.index("-vf") + 1]  # a tagged one keeps its tags instead
