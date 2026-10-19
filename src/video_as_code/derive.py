"""`derive:` bin clips (see models.DeriveSpec): files generated with
ffmpeg from another file instead of being stored.

Where they go: `<sources_dir>/.derived/<clip id>-<key>.<ext>`, where
`<key>` hashes the whole spec (input reference + every operation), so
editing the spec yields a new file name - and with it a fresh
generation - while an unchanged spec keeps pointing at the same file.
The extension follows the input's kind: a gif stays a gif, a still image
becomes a .png (lossless, whatever it was), audio becomes .m4a and
anything else (video) .mp4.

When: `validate_and_resolve` (so `build`/`render`/`validate`) generates
whatever is missing or stale as long as its input is on disk - offline,
once; `fetch` does the same right after downloading. Each output has a
`<file>.input.json` sidecar recording the input it came from (see
is_current), and older generations of the same clip are removed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import probe, sources_lock
from . import upscale as upscale_mod
from .errors import KdenliveAsCodeError
from .i18n import msg
from .models import BinClipSpec, DeriveSpec

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
    # (newer operations only when set: older specs keep their keys, and files)
    if spec.motion:
        canonical["motion"] = True
    if spec.cut:
        canonical["cut"] = [list(r) for r in spec.cut]
    if spec.upscale:
        canonical["upscale"] = spec.upscale
    if spec.intra:
        canonical["intra"] = True
    if spec.devignette is not None:
        canonical["devignette"] = spec.devignette
    if spec.rotate:
        canonical["rotate"] = spec.rotate
    if spec.fit:
        canonical["fit"] = list(spec.fit)
    if spec.fit_lighten:
        canonical["fit_lighten"] = spec.fit_lighten
    if spec.feather:
        canonical["feather"] = spec.feather
    if spec.vignette is not None:
        canonical["vignette"] = spec.vignette
    return hashlib.sha1(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:10]


def output_suffix(input_path: Path, spec: Optional[DeriveSpec] = None) -> str:
    if spec is not None and spec.motion:
        return ".mp4"  # a motion photo's video, whatever the input's extension
    if input_path.suffix.lower() == ".gif":
        return ".gif"
    kind = probe.guess_type(input_path)
    if kind == "image":
        return ".png"
    if kind == "audio":
        return ".m4a"
    return ".mp4"


def output_path(clip_id: str, spec: DeriveSpec, input_path: Path, input_ref: str, derived_dir: Path) -> Path:
    return derived_dir / f"{clip_id}-{spec_key(spec, input_ref)}{output_suffix(input_path, spec)}"


# --- generation ---------------------------------------------------------------

_FFMPEG_TIMEOUT_S = 1800


class DeriveError(KdenliveAsCodeError):
    pass


def _sidecar(output: Path) -> Path:
    return output.with_name(output.name + ".input.json")


def _identity(input_path: Path) -> dict:
    stat = input_path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def is_current(output: Path, input_path: Path) -> bool:
    """Whether `output` was generated from `input_path` as it is now. The
    spec itself is already in the file name (see spec_key), so only the
    input can have changed: same size and mtime is taken as unchanged; if
    only the mtime moved (e.g. `fetch` downloaded the same file again),
    the sha256 recorded at generation time decides, so an identical
    re-download doesn't trigger a regeneration."""
    sidecar = _sidecar(output)
    if not output.exists() or not sidecar.exists():
        return False
    try:
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    except ValueError:
        return False
    now = _identity(input_path)
    if recorded.get("size") != now["size"]:
        return False
    if recorded.get("mtime_ns") == now["mtime_ns"]:
        return True
    if recorded.get("sha256") and recorded["sha256"] == _sha256(input_path):
        sidecar.write_text(json.dumps({**recorded, **now}), encoding="utf-8")
        return True
    return False


def is_current_by_record(output: Path, locked_sha256: Optional[str]) -> bool:
    """Whether `output` is known current although its input ISN'T on disk:
    an album input that `fetch --prune` deleted after generating. The
    sha256 recorded at generation time matching the lock's for that album
    item is enough - the album still has the very same file, so nothing
    the output was made from has changed (the spec is in its name)."""
    sidecar = _sidecar(output)
    if not locked_sha256 or not output.exists() or not sidecar.exists():
        return False
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")).get("sha256") == locked_sha256
    except ValueError:
        return False


def album_input_path(source: str, bin_clips: list[BinClipSpec], sources_root: Path) -> Path:
    """Where a `derive: {source: ...}` input lives. If a plain `source:` clip
    uses the same album item, its file (stored as <capture date>_<id>, see
    sources_lock.local_name) - so an item used both ways is ONE file on
    disk, downloaded once. Otherwise the item under its own original name."""
    for clip in bin_clips:
        if clip.derive is None and clip.source == source and clip.path is not None:
            return clip.path
    return sources_root / sources_lock.source_file_name(source)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _has_audio(path: Path) -> bool:
    if not probe.ffprobe_available():
        return True  # can't tell: the optional mapping (0:a:0?) copes with none
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return bool(result.stdout.strip())


def video_info(path: Path) -> Optional[tuple[int, int, bool]]:
    """(width, height, whether its colour matrix is tagged), or None."""
    if not probe.ffprobe_available():
        return None
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,color_space",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.split() if "=" in line)
    try:
        return int(fields["width"]), int(fields["height"]), fields.get("color_space", "unknown") != "unknown"
    except (KeyError, ValueError):
        return None


def _mlt_matrix(height: int) -> str:
    """The colour matrix MLT guesses for a video whose matrix ISN'T tagged:
    by its height (BT.601 below 720 lines, BT.709 from there up)."""
    return "bt709" if height >= 720 else "bt601"


def _output_height(spec: DeriveSpec, size: tuple[int, int]) -> int:
    w, h = size
    if spec.crop:
        w, h = spec.crop[2], spec.crop[3]
    if spec.scale:
        sw, sh = spec.scale
        h = sh if sh != -1 else round(sw * h / w)
    if spec.fit:
        h = spec.fit[1]
    return h


# What `fit` can fill the space around the picture with (see _fit_filter).
FIT_BACKGROUNDS = ("blur", "black")

# How blurred `fit`'s `blur` background is: gblur's sigma as a fraction of
# the output's width - enough that nothing in it reads as a second picture.
_FIT_BLUR_SIGMA = 1 / 40


def _feather_ramp(n: int) -> str:
    """geq expression, 0 at the picture's outermost pixels to 1 at `n`
    pixels in from every side, eased (smoothstep) so the edge has no
    visible start or end."""
    m = f"clip(min(min(X,W-1-X),min(Y,H-1-Y))/{n},0,1)"
    return f"({m})*({m})*(3-2*({m}))"


def _feather_to_black(n: int) -> str:
    """`feather`: each colour channel faded out towards the edges - over
    black it looks just like a transparent edge, and works for any output
    (an .mp4 has no alpha)."""
    ramp = _feather_ramp(n)
    return f"format=gbrp,geq=r='r(X,Y)*{ramp}':g='g(X,Y)*{ramp}':b='b(X,Y)*{ramp}'"


def _gain_filter(gain: float) -> str:
    """Every RGB channel times `gain`, as colorchannelmixer steps (each
    takes at most 2)."""
    steps = []
    while gain > 1.0001:
        step = min(2.0, gain)
        steps.append(f",colorchannelmixer=rr={step:g}:gg={step:g}:bb={step:g}")
        gain /= step
    return "".join(steps)


def _fit_filter(w: int, h: int, background: str, feather: Optional[int] = None, lighten: Optional[float] = None) -> str:
    """`fit`: the picture scaled to fit inside w x h, centred, the rest
    black or - `blur` - a copy of it scaled to COVER w x h (cropped to it)
    and blurred, brightened by `lighten` (each channel times 1 + 5 x
    lighten, which keeps its colours - a dark brown turns light brown, not
    grey - so a dark picture doesn't sit on what looks like black).
    A branching graph, but still one input and one output, so it chains
    like any single filter (-vf and the gif graph alike)."""
    fg = f"scale={w}:{h}:force_original_aspect_ratio=decrease"
    if background == "blur" and feather:
        # the picture's own edges melt into the blurred copy around it
        fg += f",format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*{_feather_ramp(feather)}'"
    if background == "black":
        return f"{fg},pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    sigma = max(1.0, w * _FIT_BLUR_SIGMA)
    return (
        f"split=2[fitbg][fitfg];"
        f"[fitbg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma={sigma:g}"
        # every channel times the same gain: brighter, same hue (raising the black point instead greys it)
        + (_gain_filter(1 + 5 * lighten) if lighten else "")
        + "[fitbg2];"
        f"[fitfg]{fg}[fitfg2];[fitbg2][fitfg2]overlay=(W-w)/2:(H-h)/2"
    )


def build_command(
    spec: DeriveSpec,
    input_path: Path,
    output: Path,
    has_audio: bool,
    input_info: Optional[tuple[int, int, bool]] = None,
) -> list[str]:
    """The ffmpeg command line generating `output` (whose extension picks
    the output kind, see output_suffix) from `input_path` - of
    `input_info` (see video_info), when known, for the colour fix below."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if spec.trim:
        # Seeking before -i is fast (it doesn't decode up to the start) and,
        # since the output is re-encoded anyway, still frame-accurate.
        start, end = spec.trim
        cmd += ["-ss", f"{start:.6f}", "-t", f"{end - start:.6f}"]
    cmd += ["-i", str(input_path)]

    kind = output.suffix.lower()
    vf: list[str] = []
    if spec.rotate:
        # clockwise, before anything else: crop and the rest see the turned picture
        vf.append({90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}[spec.rotate])
    if spec.crop:
        x, y, w, h = spec.crop
        vf.append(f"crop={w}:{h}:{x}:{y}")
    if spec.scale:
        w, h = spec.scale
        # h264 + yuv420p need even dimensions: -2 is ffmpeg's "keep the
        # aspect ratio, rounded to even" (plain -1 may give an odd size)
        if kind == ".mp4":
            w, h = (-2 if w == -1 else w), (-2 if h == -1 else h)
        # setsar=1: a scale to another shape is meant to stretch the picture;
        # without it ffmpeg keeps the old shape in the sample aspect ratio, and
        # players show that again (narrower or flatter than asked for)
        vf.append(f"scale={w}:{h},setsar=1")
    if spec.devignette is not None:
        # ffmpeg's vignette darkens by cos(angle * d)^4, d being the distance
        # from the centre over the corner's (a natural lens falloff), so
        # corners at `devignette` of the centre's brightness are angle =
        # acos(devignette^(1/4)); mode=backward divides by that instead of
        # multiplying. After crop/scale: the vignette is the frame's own.
        angle = math.acos(spec.devignette ** 0.25)
        vf.append(f"vignette=angle={angle:.6f}:mode=backward")
    if spec.fit:
        vf.append(_fit_filter(*spec.fit, feather=spec.feather, lighten=spec.fit_lighten))
    if spec.feather:
        vf.append(_feather_to_black(spec.feather))
    if spec.vignette is not None:
        # the same lens falloff as devignette's, applied forward
        angle = math.acos(spec.vignette ** 0.25)
        vf.append(f"vignette=angle={angle:.6f}")
    if spec.fps:
        vf.append(f"fps={spec.fps:g}")
    if spec.reverse:
        vf.append("reverse")
    if kind == ".mp4" and input_info is not None and not input_info[2]:
        # An UNTAGGED video crossing 720 lines (an HD one scaled down for a
        # light copy, say) changes MLT's guess of its colour matrix (see
        # _mlt_matrix): convert, so the result plays as the original did.
        # (A tagged one - phones tag theirs - keeps its tags through ffmpeg
        # and plays the same at any size: measured on two MLT versions,
        # where converting it instead made one of them worse.)
        before, after = _mlt_matrix(input_info[1]), _mlt_matrix(_output_height(spec, input_info[:2]))
        if before != after:
            vf.append(f"scale=in_color_matrix={before}:out_color_matrix={after}")
    af: list[str] = []
    if spec.reverse:
        af.append("areverse")
    if spec.volume is not None:
        af.append(f"volume={spec.volume:g}")

    if kind == ".m4a":
        cmd += ["-vn"]
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    elif kind == ".gif":
        # A per-clip palette keeps a gif's colors close to the input's
        # (ffmpeg's default 256-color palette visibly bands photos).
        chain = ",".join(vf) + "," if vf else ""
        cmd += [
            "-filter_complex", f"[0:v]{chain}split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse",
            "-an", "-loop", "0",
        ]
    elif kind == ".png":
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd += ["-frames:v", "1"]
    else:  # .mp4
        vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        cmd += ["-map", "0:v:0", "-vf", ",".join(vf)]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
        if spec.intra:
            cmd += ["-g", "1", "-bf", "0"]  # every frame a keyframe (see DeriveSpec.intra)
        if has_audio:
            cmd += ["-map", "0:a:0?"]
            if af:
                cmd += ["-af", ",".join(af)]
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(output))
    return cmd


# A motion photo's embedded video, as its XMP describes it: the Container
# item with Semantic MotionPhoto (current Pixels) gives its Length from the
# end of the file; older ones say MicroVideoOffset (also from the end).
_MOTION_ITEM_RE = re.compile(rb'Item:Mime="video/mp4"\s+Item:Semantic="MotionPhoto"\s+Item:Length="(\d+)"')
_MICRO_VIDEO_RE = re.compile(rb'MicroVideoOffset="(\d+)"')


def extract_motion_video(clip_id: str, photo: Path, output: Path) -> None:
    """Writes the video inside motion photo `photo` to `output` (an .mp4),
    found through its XMP or, failing that, the first MP4 header after
    the JPEG. Raises DeriveError if there's none."""
    data = photo.read_bytes()
    head = data[:262144]  # the XMP sits near the start
    match = _MOTION_ITEM_RE.search(head) or _MICRO_VIDEO_RE.search(head)
    start = len(data) - int(match.group(1)) if match else None
    if start is None or data[start + 4:start + 8] != b"ftyp":
        found = data.find(b"ftyp", data.find(b"\xff\xd9") + 2)
        start = found - 4 if found >= 4 else None
    if start is None or data[start + 4:start + 8] != b"ftyp":
        raise DeriveError(msg("derive.not_a_motion_photo", id=clip_id, path=photo))
    output.write_bytes(data[start:])


def _kept_ranges(spec: DeriveSpec) -> list[tuple[float, Optional[float]]]:
    """The input's [start, end) ranges that survive `trim` and `cut`, in
    order (end None: to the input's end)."""
    start, end = spec.trim if spec.trim else (0.0, None)
    kept: list[tuple[float, Optional[float]]] = []
    for a, b in spec.cut or ():
        if end is not None and a >= end:
            break
        if a > start:
            kept.append((start, a))
        start = max(start, b)
    if end is None or start < end:
        kept.append((start, end))
    return kept


def build_cut_command(spec: DeriveSpec, input_path: Path, output: Path, has_audio: bool) -> list[str]:
    """`trim` + `cut` as their own lossless pass (FFV1 + PCM in Matroska):
    each kept range trimmed and the pieces concatenated, picture and sound
    alike. (ffmpeg's aselect would be the one-pass way, but it doesn't
    drop anything on ffmpeg 8 - checked: even `aselect=0` keeps it all.)
    The other operations then run on this, as on any input."""
    parts: list[str] = []
    labels: list[str] = []
    for i, (a, b) in enumerate(_kept_ranges(spec)):
        bounds = f"start={a:.6f}" + (f":end={b:.6f}" if b is not None else "")
        parts.append(f"[0:v]trim={bounds},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim={bounds},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    n = len(_kept_ranges(spec))
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}"
    graph += "[v][a]" if has_audio else "[v]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(input_path), "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "pcm_s16le"]
    return cmd + ["-c:v", "ffv1", str(output)]


def _ffmpeg_ops(spec: DeriveSpec) -> bool:
    return bool(
        spec.rotate or spec.trim or spec.cut or spec.crop or spec.scale or spec.devignette is not None or spec.fit
        or spec.feather or spec.vignette is not None or spec.fps or spec.reverse or spec.volume is not None or spec.intra
    )


def _run(clip_id: str, cmd: list[str], output: Path) -> None:
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=_FFMPEG_TIMEOUT_S)
    except subprocess.CalledProcessError as exc:
        output.unlink(missing_ok=True)
        raise DeriveError(
            msg("derive.ffmpeg_failed", id=clip_id, stderr=exc.stderr.decode(errors="replace").strip())
        ) from exc
    except subprocess.TimeoutExpired as exc:
        output.unlink(missing_ok=True)
        raise DeriveError(msg("derive.ffmpeg_timeout", id=clip_id)) from exc


def _run_ffmpeg(clip_id: str, spec: DeriveSpec, input_path: Path, output: Path) -> None:
    info = video_info(input_path) if output.suffix.lower() == ".mp4" else None
    _run(clip_id, build_command(spec, input_path, output, _has_audio(input_path), info), output)


def generate(
    clip_id: str,
    spec: DeriveSpec,
    input_path: Path,
    output: Path,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Generates `output` from `input_path` in up to four passes - a
    motion photo's video taken out first (`motion`), then `trim` +
    `cut` into a lossless intermediate when there's a `cut`
    (build_cut_command), then ffmpeg's operations, then `upscale`
    (Real-ESRGAN) - into a temporary file next to `output`, renamed into
    place only once complete (an interrupted run never leaves a file that
    looks finished). Then records the input's identity for is_current and
    removes this clip's older generations (other spec hashes)."""
    if not probe.ffmpeg_available():
        raise DeriveError(msg("derive.ffmpeg_unavailable", id=clip_id))
    output.parent.mkdir(parents=True, exist_ok=True)
    # the extension must stay last: ffmpeg picks the output format from it
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    intermediates: list[Path] = []
    source = input_path
    try:
        if spec.motion:
            motion_file = output.with_name(f"{output.stem}.motion.mp4")
            intermediates.append(motion_file)
            extract_motion_video(clip_id, input_path, motion_file)
            source, spec = motion_file, dataclasses.replace(spec, motion=False)
        if spec.cut:
            cut_file = output.with_name(f"{output.stem}.cut.mkv")
            intermediates.append(cut_file)
            _run(clip_id, build_cut_command(spec, input_path, cut_file, _has_audio(input_path)), cut_file)
            source, spec = cut_file, dataclasses.replace(spec, trim=None, cut=None)
        if spec.upscale:
            if output.suffix.lower() not in (".png", ".gif"):
                raise DeriveError(msg("derive.upscale_stills_only", id=clip_id))
            if _ffmpeg_ops(spec):  # the other operations first, on the small picture
                pre = output.with_name(f"{output.stem}.pre{output.suffix}")
                intermediates.append(pre)
                _run_ffmpeg(clip_id, spec, source, pre)
                source = pre
            try:
                upscale_mod.upscale(clip_id, source, tmp, on_progress)
            except (upscale_mod.UpscaleError, subprocess.SubprocessError, OSError) as exc:
                tmp.unlink(missing_ok=True)
                raise DeriveError(str(exc)) from exc
        else:
            _run_ffmpeg(clip_id, spec, source, tmp)
    finally:
        for path in intermediates:
            path.unlink(missing_ok=True)
    tmp.replace(output)
    _sidecar(output).write_text(
        json.dumps({"input": input_path.name, **_identity(input_path), "sha256": _sha256(input_path)}),
        encoding="utf-8",
    )
    # (matched on the exact "<id>-<10 hex>." shape, so clip "a" never
    # touches clip "a-b"'s files)
    own = re.compile(rf"{re.escape(clip_id)}-[0-9a-f]{{10}}\.")
    for old in output.parent.iterdir():
        if own.match(old.name) and old.name not in (output.name, _sidecar(output).name):
            old.unlink(missing_ok=True)


def ensure_all(
    bin_clips: list[BinClipSpec],
    sources_root: Path,
    errors: list[str],
    on_generate: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    locked_sha256: Optional[dict[str, str]] = None,
) -> list[str]:
    """Generates every derived clip whose file is missing or stale, inputs
    first (a `clip:` input may itself be derived). A missing input is
    appended to `errors` - pointing at `fetch` when it's an album item -
    and so is an ffmpeg failure; everything else still gets generated.
    `locked_sha256` (album name -> sha256, from the lock) lets an output
    whose album input was pruned count as current (is_current_by_record).
    Returns the ids generated."""
    by_id = {c.id: c for c in bin_clips}
    done: dict[str, bool] = {}  # id -> its file is ready
    generated: list[str] = []

    def ready(clip: BinClipSpec) -> bool:
        if clip.derive is None:
            return True
        if clip.id in done:
            return done[clip.id]
        done[clip.id] = False  # (cycles were already rejected at parse time)
        spec = clip.derive
        if spec.clip is not None:
            ref = by_id[spec.clip]
            if not ready(ref):
                return False
            input_path = ref.path
        elif spec.source is not None:
            input_path = album_input_path(spec.source, bin_clips, sources_root)
        else:
            input_path = spec.path
        if not input_path.exists():
            if spec.source is not None and is_current_by_record(clip.path, (locked_sha256 or {}).get(spec.source)):
                done[clip.id] = True  # its input was pruned; the album still has it unchanged
                return True
            if spec.source is not None:
                errors.append(msg("validation.derive_input_not_fetched", id=clip.id, path=input_path))
            elif spec.clip is None:
                errors.append(msg("validation.derive_input_missing", id=clip.id, path=input_path))
            return False
        if not is_current(clip.path, input_path):
            if on_generate:
                on_generate(clip.id)
            try:
                generate(clip.id, spec, input_path, clip.path, on_progress)
            except DeriveError as exc:
                errors.append(str(exc))
                return False
            generated.append(clip.id)
        done[clip.id] = True
        return True

    for clip in bin_clips:
        ready(clip)
    return generated
