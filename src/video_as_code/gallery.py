"""`video-as-code gallery`: a page, opened in the browser, with every
transition and effect a project YAML can ask for - each with the YAML
that asks for it, to copy, and a preview of what it does.

The previews are real: each is a tiny project (CATALOG) rendered by
`video-as-code render` itself, so the page shows exactly what a render
gives - and doubles as a check that every one of them still renders (a
broken one shows its error instead). They play over generated sample
pictures (or `--with` the user's own still/video). Rendered once, then
cached under the user cache folder, keyed by the project, the samples,
this package's own code and the melt it ran with: the page opens at
once the next time, and again after any of those changes, only what
changed is rendered.

The wipe shapes (`luma:` - this project's own and OpenShot's, lumas.py)
are the exception: 424 renders would take long, and they're plain
masks, so the page plays them itself, each over and over while it's
on screen, from one sample picture to the other: a pixel changes as
the wipe passes its grey level, darkest first, as MLT's `luma` does
(softened a little). Their thumbnails are made from the user's own
openshot-qt install, never checked in: OpenShot's maps are GPL-3.0+.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from . import gltransitions, lumas, meltpass, probe
from .errors import KdenliveAsCodeError
from . import i18n
from .i18n import msg
from .mltdata import default_cache_root

WIDTH, HEIGHT = 320, 180
THUMB_SIZE = (WIDTH, HEIGHT)  # the wipe shapes play as big as the videos
GALLERY_DIR = "gallery"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


@dataclass(frozen=True)
class Entry:
    section: str  # "transitions", "clip", "derive", "title"
    name: str
    snippet: str  # the YAML shown, to copy
    project: str  # the whole project rendered for the preview, with {a} {b} {video} ... placeholders
    title: str = ""  # melt's own title and description, for its catalog's entries
    description: str = ""
    params: tuple = ()  # melt's description of its parameters (see _tweaks)
    tweaked: Optional[str] = None  # with which values its preview is, when not its defaults
    melt_catalog: bool = False  # one of melt's services as they come (see melt_entries)
    render: bool = True  # (an audio filter has nothing to show)
    source: str = "a"  # the sample a clip entry plays (see _clip)
    unavailable: Optional[str] = None  # why it can't work in a render of this project's (see _unavailable)


def _indent(text: str, spaces: int) -> str:
    return "\n".join(" " * spaces + line for line in text.strip("\n").splitlines())


_HEADER = f"project: {{id: gallery, title: Gallery, fps: 25, width: {WIDTH}, height: {HEIGHT}}}\n"


def _scene(name: str, keys: str, section: str = "transitions") -> Entry:
    """A transition between two stills, as a `scenes:` entry."""
    project = _HEADER + f"""scenes:
  - scene: s
{_indent(keys, 4)}
    transition_duration: 1.2
    clips: ["{{a}}", "{{b}}"]
    duration: 4.4
"""
    return Entry(section, name, keys.strip(), project)


def _clip(name: str, keys: str, source: str = "a", background: bool = False) -> Entry:
    """Clip keys on one timeline clip - `background`: over a still on a
    track below (picture-in-picture, chroma key)."""
    below = (
        """    - type: video
      clips:
        - {clip: b, start: 0, duration: 3}
"""
        if background
        else ""
    )
    interlaced = '  - {id: interlaced, path: "{interlaced}"}\n' if source == "interlaced" else ""
    project = _HEADER + f"""bin:
  - {{id: a, path: "{{a}}"}}
  - {{id: b, path: "{{b}}"}}
  - {{id: video, path: "{{video}}"}}
  - {{id: shaky, path: "{{shaky}}"}}
  - {{id: green, path: "{{green}}"}}
{interlaced}timeline:
  tracks:
{below}    - type: video
      clips:
        - clip: {source}
          start: 0
{_indent(keys, 10)}
"""
    if "duration:" not in keys and "out:" not in keys:
        project += "          duration: 3\n"
    return Entry("clip", name, keys.strip(), project, source=source)


def _derive(name: str, keys: str, source: str = "portrait") -> Entry:
    project = _HEADER + f"""bin:
  - id: d
    derive:
      path: "{{{source}}}"
{_indent(keys, 6)}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: d, start: 0, duration: 3}}
"""
    return Entry("derive", name, "derive:\n" + _indent(keys, 2), project)


def _title(name: str, keys: str) -> Entry:
    project = _HEADER + f"""bin:
  - {{id: a, path: "{{a}}"}}
  - id: card
    type: title
{_indent(keys, 4)}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: a, start: 0, duration: 3}}
    - type: video
      clips:
        - {{clip: card, start: 0, duration: 3, fade_in: 0.5}}
"""
    return Entry("title", name, "type: title\n" + keys.strip(), project)


# the clip keys that change it as it plays - fades, keyframed motion and
# colour, speed and freezes: a list of their own, apart from those that
# hold over the whole clip
_OVER_TIME = {
    "fade_in / fade_out", "speed", "speed, backwards", "speed_ramp", "boomerang", "freeze_at",
    "position_keyframes", "ken_burns_from / to", "color_grade_keyframes",
}

CATALOG: tuple[Entry, ...] = (
    _scene("dissolve", "transition: dissolve"),
    _scene("wipe", "transition: wipe\nluma: radial"),
    _scene("wipe, soft edge", "transition: wipe\nluma: openshot/twirl_12\nluma_softness: 0.3"),
    _scene("affine zoom", "transition: affine\nstyle: zoom"),
    _scene("affine slide", "transition: affine\nstyle: slide"),
    _scene("iris", "transition: iris"),
    _scene("flip", "transition: flip"),
    _scene("glitch", "transition: glitch"),
    _scene("delaygrab", "transition: delaygrab"),
    _scene("cairogradient", "transition: cairogradient"),
    _scene("melt/<service>", 'transition: melt/composite\ntransition_properties: {geometry: "0=50%/50%:0%x0%;-1=0/0:100%x100%"}'),
    _clip("fade_in / fade_out", "fade_in: 1\nfade_out: 1"),
    _clip("speed", "speed: 3", source="video"),
    _clip("speed, backwards", "speed: -1", source="video"),
    _clip("speed_ramp", "duration: 3\nspeed_ramp: [{at: 0, speed: 4}, {at: 1, speed: 0.5}]", source="video"),
    _clip("boomerang", "duration: 1.5\nboomerang: true", source="video"),
    _clip("freeze_at", "freeze_at: 1\nfreeze_after: true", source="video"),
    _clip("crop", "crop: {left: 80, right: 80}"),
    _clip("position / size", "position: [170, 20]\nsize: [130, 73]", background=True),
    _clip(
        "position_keyframes",
        "position_keyframes:\n  - {at: 0, position: [0, 0], size: [320, 180]}\n  - {at: 2, position: [180, 100], size: [128, 72]}",
        background=True,
    ),
    _clip("ken_burns_from / to", "ken_burns_from: [0, 0, 100, 100]\nken_burns_to: [40, 30, 40, 40]"),
    _clip("color_grade", "color_grade: {lift: [0.06, 0.03, 0], gain: [1.15, 1.0, 0.8]}"),
    _clip("color_grade_keyframes", "color_grade_keyframes:\n  - {at: 0, gain: [1, 1, 1]}\n  - {at: 2, gain: [0.6, 0.8, 1.3]}"),
    _clip("old_film", "old_film: true"),
    _clip("film_dust", "film_dust: true"),
    _clip("film_dust, dots", "film_dust: {style: dots, size: 4, count: 10}"),
    _clip("film_scratches", "film_scratches: true"),
    _clip("film_grain", "film_grain: {noise: 30}"),
    _clip("stabilize", "stabilize: true", source="shaky"),
    _clip("focus_rect", "focus_rect: [30, 20, 40, 60]\nfocus_blur: 6"),
    _clip("chroma_key", 'chroma_key: "#00ff00"', source="green", background=True),
    _clip("overlay_text", 'overlay_text: "#timecode#"\noverlay_font_size: 24'),
    # (the sample LUT is made with the samples: see _teal_orange)
    replace(_clip("lut", "lut: {lut}"), snippet="lut: looks/teal-orange.cube"),
    _clip("melt", "melt:\n  - frei0r.cartoon: {diffspace: 0.4}\n  - avfilter.hue: {av.h: \"0=0;74=180\"}"),
    _derive("fit, blur", "fit: {size: [320, 180], background: blur}"),
    _derive("fit, black", "fit: {size: [320, 180], background: black}"),
    _derive("fit, card", "fit: {size: [320, 180], background: blur, margin: 12, radius: 10, shadow: 0.45, dim: 0.2}"),
    _derive("feather", "scale: [320, 180]\nfeather: 24", source="a"),
    _derive("vignette", "vignette: 0.5", source="a"),
    _derive("rotate", "rotate: 90", source="a"),
    _title("title card", 'text: "Hello"\nfont: "DejaVu Serif"\nfont_size: 44\nfont_weight: bold\nshadow: true'),
    _title("title card, outline", 'text: "Hello"\nfont: "Noto Serif"\nfont_size: 44\nitalic: true\noutline: 2\noutline_color: [180, 30, 30, 255]'),
)

CATALOG = tuple(replace(e, section="clip_time") if e.section == "clip" and e.name in _OVER_TIME else e for e in CATALOG)

SECTIONS = ("transitions", "lumas", "gl", "melt_transitions", "clip", "clip_time", "derive", "title", "melt_filters", "melt_analysis", "melt_audio")
# the page's two tabs: what goes between clips, and what goes on one
TABS = {"transitions": ("transitions", "lumas", "gl", "melt_transitions"), "effects": ("clip", "clip_time", "derive", "title", "melt_filters", "melt_analysis", "melt_audio")}


def lut_entries() -> tuple[Entry, ...]:
    """One per LUT in the user's own folder (schema.lut_dirs), by name - its
    file in the project text, so a changed LUT renders again."""
    from .schema import lut_dirs

    folder = lut_dirs(Path("."))[-1]
    if not folder.is_dir():
        return ()
    out = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() in (".cube", ".3dl", ".dat", ".m3d"):
            entry = _clip(f"lut: {path.stem}", f"lut: {path.stem}")
            stamp = f"# {path.name} {path.stat().st_size} {path.stat().st_mtime_ns}\n"
            out.append(replace(entry, project=stamp + entry.project, description=msg("gallery.about_user_lut", path=path)))
    return tuple(out)


def gl_entries() -> tuple[Entry, ...]:
    """One per gl-transitions transition, with its defaults - once its
    plugin is built (gltransitions.py); none before."""
    if not gltransitions.installed():
        return ()
    return tuple(
        replace(_scene(name, f"transition: gl\ngl: {name}", section="gl"), description=info.get("description", ""))
        for name, info in gltransitions.catalog().items()
    )


def _audio(name: str, keys: str) -> Entry:
    """Keys on a clip of the sample sound, on an audio track."""
    project = _HEADER + f"""bin:
  - {{id: sound, path: "{{sound}}"}}
timeline:
  tracks:
    - type: audio
      clips:
        - clip: sound
          start: 0
          duration: 4
{_indent(keys, 10)}
"""
    return Entry("melt_audio", name, keys.strip(), project)


def _describe_service(kind: str, service: str) -> dict:
    """melt's title, description, tags and parameters of one service."""
    meta = meltpass._query(f"{kind}={service}")
    meta = meta if isinstance(meta, dict) else {}
    params = []
    for param in meta.get("parameters") or []:
        if not isinstance(param, dict) or param.get("identifier") is None:
            continue
        # frei0r's are numbered; MLT takes each by its name too (meltpass.py)
        name = param.get("title") if service.startswith("frei0r.") and param.get("title") else param["identifier"]
        params.append({k: param.get(k) for k in ("type", "default", "minimum", "maximum")} | {"name": str(name)})
    return {
        "title": str(meta.get("title") or ""),
        "description": " ".join(str(meta.get("description") or "").split()),
        "tags": [str(t) for t in meta.get("tags") or []],
        "params": params,
    }


def _melt_catalog(cache: Optional[Path] = None) -> dict[str, dict[str, dict]]:
    """{"filter"|"transition": {service: {title, description, tags}}} of
    the installed melt - asked once per melt (675 `melt -query` take
    minutes), kept in `cache`."""
    key = f"catalog 2\n{probe.find_melt()}\n{_melt_version()}"
    if cache is not None:
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
            if stored.get("melt") == key:
                return stored["services"]
        except (OSError, ValueError, AttributeError, KeyError):
            pass
    pairs = [(kind, service) for kind in ("transition", "filter") for service in sorted(meltpass._services(kind) or ())]

    with ThreadPoolExecutor(max_workers=8) as pool:
        described = list(pool.map(lambda pair: _describe_service(*pair), pairs))
    services: dict[str, dict[str, dict]] = {"transition": {}, "filter": {}}
    for (kind, service), info in zip(pairs, described):
        services[kind][service] = info
    if cache is not None:
        cache.write_text(json.dumps({"melt": key, "services": services}, indent=1), encoding="utf-8")
    return services


def melt_entries(cache: Optional[Path] = None) -> tuple[Entry, ...]:
    """Every filter and transition the installed melt has, as `melt:` and
    `melt/<service>` (meltpass.py) take them, at their defaults: the video
    ones over the sample pictures, the audio filters over the sample
    sound. Their previews depend on melt and the samples only, not on
    this package's code (melt_catalog)."""
    entries = []
    for kind, services in _melt_catalog(cache).items():
        for service, meta in services.items():
            if service == gltransitions.SERVICE:  # (it needs a shader: its own section)
                continue
            info = {
                "title": meta["title"], "description": meta["description"], "melt_catalog": True,
                "params": tuple(tuple(sorted(p.items(), key=lambda kv: kv[0])) for p in meta.get("params", ())),
            }
            if kind == "transition":
                snippet = f"transition: melt/{service}"
                why = _unavailable(service)
                if why:
                    entries.append(Entry("melt_transitions", service, snippet, "", render=False, unavailable=why, **info))
                    continue
                if meta["tags"] == ["Audio"]:  # (a sound transition: mix - listed)
                    entries.append(Entry("melt_audio", service, snippet, "", render=False, **info))
                else:
                    entries.append(replace(_scene(service, f'transition: "melt/{service}"', section="melt_transitions"), snippet=snippet, **info))
                continue
            snippet = f"melt:\n  - {service}"
            why = _unavailable(service)
            if why:
                entries.append(Entry("melt_filters", service, snippet, "", render=False, unavailable=why, **info))
                continue
            if meta["tags"] == ["Audio"]:
                entries.append(replace(_audio(service, f'melt: ["{service}"]'), snippet=snippet, **info))
            else:
                source = "interlaced" if _DEINTERLACERS.search(service) else "video"
                section = "melt_analysis" if _ANALYSIS.match(service) else "melt_filters"
                entries.append(replace(_clip(service, f'melt: ["{service}"]', source=source), section=section, snippet=snippet, **info))
    return tuple(entries)


# melt's filters play over the video sample, not a still: many work on
# what moves (a still gave baltan 9 different frames in 75, nervous one);
# those that undo interlacing over a sample whose lines are combed - and
# flagged progressive, or melt itself would deinterlace it first
_DEINTERLACERS = re.compile(r"yadif|bwdif|w3fdif|estdif|kerndeint|mcdeint|nnedi|deinterlace|telecide|fieldorder|fieldmatch|phase|pullup|detelecine|dejudder|separatefields|weave")
# ffmpeg filters that run on a device: they take frames already on it,
# which melt's bridge to ffmpeg (avfilter.*) doesn't give them
_HARDWARE = (
    ("cuda", "CUDA (NVIDIA)"), ("npp", "NPP (NVIDIA)"), ("vaapi", "VA-API"), ("vulkan", "Vulkan"),
    ("opencl", "OpenCL"), ("qsv", "Intel Quick Sync"), ("vdpau", "VDPAU"), ("hwupload", "a hardware device"),
    ("hwdownload", "a hardware device"), ("hwmap", "a hardware device"),
)


# filters that measure rather than change: scopes and graphs drawn over the
# picture, detectors that only note what they find (edgedetect, despite its
# name, draws the edges: an effect)
_ANALYSIS = re.compile(
    r"^(avfilter\.(datascope|oscilloscope|pixscope|vectorscope|waveform|histogram|thistogram|ciescope|graphmonitor"
    r"|showinfo|showpalette|signalstats|(?!edge)\w*detect|bbox|idet|readeia608|readvitc|entropy|scdet|siti|\w*stats"
    r"|metadata|sidedata)|frei0r\.(facedetect|(measure_)?pr0be|(measure_)?pr0file|rgbparade|vectorscope))$"
)


def _unavailable(service: str) -> Optional[str]:
    """Why `service` can't work in a render of this project's, if it can't:
    an ffmpeg filter for a device, or one of movit's - MLT's GPU mode,
    which renders GPU textures, not pictures: in melt's plain render
    their "pictures" are a texture's bytes, stripes of colour."""
    if service.startswith("movit."):
        return msg("gallery.needs_movit")
    api = next((api for key, api in _HARDWARE if key in service), None)
    return msg("gallery.needs_hardware", api=api) if api else None


_BASELINES = {source: _clip(f"unfiltered {source}", "", source=source) for source in ("video", "interlaced")}
_SOUND_BASELINE = _audio("original", "")  # the sample sound as it is - shown, to compare with
_SAME_DB = 40.0  # frames this close (PSNR) look the same


def _frames_psnr(a: Path, at_a: float, b: Path, at_b: float) -> float:
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "info", "-ss", str(at_a), "-i", str(a), "-ss", str(at_b), "-i", str(b),
             "-frames:v", "1", "-lavfi", "[0][1]psnr", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    found = re.search(r"average:(inf|[\d.]+)", result.stderr)
    if not found:
        return 0.0
    return float("inf") if found.group(1) == "inf" else float(found.group(1))


def _samples_of(path: Path) -> list[int]:
    data = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, timeout=60,
    ).stdout
    return [int.from_bytes(data[i:i + 2], "little", signed=True) for i in range(0, len(data) - 1, 2)]


def _sounds_the_same(a: Path, b: Path) -> bool:
    """Whether two renders' sound differs by under 1% of the level."""
    try:
        x, y = _samples_of(a), _samples_of(b)
    except (OSError, subprocess.TimeoutExpired):
        return False
    n = min(len(x), len(y))
    if not n:
        return False
    level = (sum(v * v for v in y[:n]) / n) ** 0.5 or 1.0
    diff = (sum((x[i] - y[i]) ** 2 for i in range(n)) / n) ** 0.5
    return diff < 0.01 * level


def _unchanged(entry: Entry, out: Path, baselines: dict[str, Path], sound_baseline: Path) -> bool:
    """Whether `entry`, at its defaults, changes nothing: a filter's frame
    like the unfiltered one (its sound like the sample's, for an audio
    one), a transition's middle like its start or end."""
    if entry.section == "melt_audio":
        return _sounds_the_same(out, sound_baseline)
    if entry.section == "melt_transitions":
        middle = 2.2  # (_scene: 4.4 s, the clips overlapping around it)
        return _frames_psnr(out, middle, out, 0.4) > _SAME_DB or _frames_psnr(out, middle, out, 4.2) > _SAME_DB
    return _frames_psnr(out, 1.5, baselines[entry.source], 1.5) > _SAME_DB


def _tweaks(entry: Entry, tries: int = 3) -> list[dict]:
    """Other values to show `entry` with, when at its defaults it changes
    nothing: one parameter at a time, a number toward the far end of its
    range from its default (3/4 of the way up from a low one, 1/4 from a
    high one), a true/false the other way - those melt gives a range or a
    type for; at most `tries`."""
    out = []
    for param in (dict(p) for p in entry.params):
        if "DEPRECATED" in param["name"]:
            continue
        kind, default = param.get("type"), param.get("default")
        low, high = param.get("minimum"), param.get("maximum")
        if kind == "boolean":
            value = not bool(default not in (None, 0, "0", False))
        elif kind in ("float", "integer", "double") and isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
            try:
                start = float(default) if default is not None else float(low)
            except (TypeError, ValueError):
                start = float(low)
            value = low + (high - low) * (0.75 if start - low <= (high - low) / 2 else 0.25)
            value = round(value) if kind == "integer" else round(value, 3)
            if value == start:
                continue
        else:
            continue
        out.append({param["name"]: value})
        if len(out) == tries:
            break
    return out


def _with_values(entry: Entry, values: dict) -> Entry:
    """`entry` with these property values - its project and its snippet."""
    flow = json.dumps(values)
    if entry.section == "melt_transitions":
        keys = f'transition: "melt/{entry.name}"\ntransition_properties: {flow}'
        project = _scene(entry.name, keys, section=entry.section).project
        snippet = f"transition: melt/{entry.name}\ntransition_properties: {flow}"
    else:
        keys = f"melt: [{json.dumps({entry.name: values})}]"
        if entry.section == "melt_audio":
            project = _audio(entry.name, keys).project
        else:
            project = _clip(entry.name, keys, source=entry.source).project
        snippet = f"melt:\n  - {entry.name}: {flow}"
    shown = ", ".join(f"{k}: {json.dumps(v)}" for k, v in values.items())
    return replace(entry, project=project, snippet=snippet, tweaked=shown)


@functools.lru_cache(maxsize=None)
def _melt_version() -> str:
    melt = probe.find_melt()
    try:
        result = subprocess.run([str(melt), "--version"], capture_output=True, text=True, env=probe.melt_env(), timeout=30)
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return ""


# --- samples -----------------------------------------------------------------

def _ffmpeg(args: list[str]) -> None:
    try:
        result = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise KdenliveAsCodeError(msg("gallery.ffmpeg_unavailable"))
    if result.returncode != 0:
        raise KdenliveAsCodeError(msg("gallery.ffmpeg_failed", stderr=result.stderr.strip()))


# Photos the previews play over, when the system has them: Ubuntu's own
# wallpapers (ubuntu-wallpapers), read where they're installed, never
# checked in - a bluebell wood and a red maple leaf for the two stills,
# a pan across an aurora for the video. Otherwise, generated pictures.
BACKGROUNDS_DIR = Path("/usr/share/backgrounds")
_PHOTOS = {"a": "mendhak-Bluebells_Suspended_In_Time.jpg", "b": "mendhak-Red_Acer.jpg", "video": "mizuno-as-Big_Dipper.jpg"}
_SAMPLE_SIZE = (960, 540)  # bigger than the previews: a zoom or a crop stays sharp


def _photos() -> Optional[dict[str, Path]]:
    found = {key: BACKGROUNDS_DIR / name for key, name in _PHOTOS.items()}
    return found if all(path.is_file() for path in found.values()) else None


def _recipes() -> dict[str, list[str]]:
    """file name -> ffmpeg arguments that make it."""
    w, h = _SAMPLE_SIZE
    s = f"{w}x{h}"
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    green = [  # a red square moving across a green screen - for chroma_key, photos or not
        "-f", "lavfi", "-i", f"color=c=0x00ff00:s={s}:rate=25",
        "-f", "lavfi", "-i", "color=c=0xd04040:s=240x240:rate=25",
        "-filter_complex", "[0][1]overlay=x='90+t*150':y=150", "-t", "4", "-pix_fmt", "yuv420p",
    ]
    # the sound the audio filters play over: a plucked arpeggio (C E G C,
    # a note every half second, each dying away) with a soft noise hit on
    # every second beat - echoes, reverbs and distortions all show on it
    note = "if(lt(mod(t,2),0.5),261.63,if(lt(mod(t,2),1),329.63,if(lt(mod(t,2),1.5),392,523.25)))"
    pluck = f"0.5*sin(2*PI*{note}*t)*exp(-5*mod(t,0.5))"
    hit = "0.25*(random(0)*2-1)*exp(-30*mod(t-0.5,1))*gte(t,0.5)"
    sound = [
        "-f", "lavfi", "-i", f"aevalsrc=exprs='{pluck}+{hit}|{pluck}+{hit}':s=44100:d=4",
        "-c:a", "aac", "-b:a", "128k",
    ]
    photos = _photos()
    if photos is None:
        return {
            "a.png": ["-f", "lavfi", "-i", f"mandelbrot=s={s}:start_scale=1.2", "-frames:v", "1"],
            "b.png": ["-f", "lavfi", "-i", f"gradients=s={s}:c0=0x1f6f8b:c1=0xf2c14e:c2=0xd1495b:nb_colors=3:seed=7", "-frames:v", "1"],
            "portrait.png": ["-f", "lavfi", "-i", "mandelbrot=s=360x600:start_scale=0.9", "-frames:v", "1"],
            "video.mp4": ["-f", "lavfi", "-i", f"mandelbrot=s={s}:rate=25", "-t", "10", "-pix_fmt", "yuv420p"],
            "shaky.mp4": [
                "-f", "lavfi", "-i", "testsrc2=s=1100x620:rate=25", "-t", "4",
                "-vf", f"crop={w}:{h}:x='70+60*sin(t*9)':y='40+35*sin(t*13)'", "-pix_fmt", "yuv420p",
            ],
            "green.mp4": green,
            "sound.m4a": sound,
            "interlaced.mp4": [
                "-f", "lavfi", "-i", "testsrc2=s=1600x540:rate=50", "-t", "4",
                "-vf", f"crop={w}:{h}:x='(iw-ow)*t/4':y=0,tinterlace=mode=interleave_top,setfield=prog",
                "-r", "25", "-pix_fmt", "yuv420p",
            ],
        }
    return {
        "a.png": ["-i", str(photos["a"]), "-frames:v", "1", "-vf", fill],
        "b.png": ["-i", str(photos["b"]), "-frames:v", "1", "-vf", fill],
        "portrait.png": ["-i", str(photos["b"]), "-frames:v", "1", "-vf", "scale=-2:640,crop=400:640"],
        "video.mp4": [  # a slow pan across the aurora, left to right
            "-loop", "1", "-framerate", "25", "-i", str(photos["video"]), "-t", "10",
            "-vf", f"scale={w * 5 // 3}:{h * 5 // 3}:force_original_aspect_ratio=increase,crop={w * 5 // 3}:{h * 5 // 3},"
                   f"crop={w}:{h}:x='(iw-ow)*t/10':y='(ih-oh)/2'",
            "-pix_fmt", "yuv420p",
        ],
        "shaky.mp4": [  # the wood, shaken - for stabilize
            "-loop", "1", "-framerate", "25", "-i", str(photos["a"]), "-t", "4",
            "-vf", f"scale=1100:620:force_original_aspect_ratio=increase,crop=1100:620,"
                   f"crop={w}:{h}:x='70+60*sin(t*9)':y='40+35*sin(t*13)'",
            "-pix_fmt", "yuv420p",
        ],
        "green.mp4": green,
        "sound.m4a": sound,
        "interlaced.mp4": [  # a fast pan across the aurora, its fields woven: combed
            "-loop", "1", "-framerate", "50", "-i", str(photos["video"]), "-t", "4",
            "-vf", f"scale={w * 2}:{h * 5 // 3}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 5 // 3},"
                   f"crop={w}:{h}:x='(iw-ow)*t/4':y='(ih-oh)/2',tinterlace=mode=interleave_top,setfield=prog",
            "-r", "25", "-pix_fmt", "yuv420p",
        ],
    }


def _teal_orange(path: Path, size: int = 17) -> None:
    """A "teal and orange" look as a .cube LUT, for `lut:`'s preview:
    shadows toward teal, highlights toward orange, a little more colour."""
    lines = ["TITLE \"teal-orange\"", f"LUT_3D_SIZE {size}"]
    teal, orange = (0.0, 0.5, 0.55), (1.0, 0.62, 0.3)
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rgb = [c / (size - 1) for c in (r, g, b)]
                luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                tint = [t + (o - t) * luma for t, o in zip(teal, orange)]
                out = [luma + (c - luma) * 1.15 for c in rgb]  # saturation
                out = [c * 0.8 + t * 0.2 for c, t in zip(out, tint)]
                lines.append(" ".join(f"{min(1.0, max(0.0, c)):.6f}" for c in out))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_samples(folder: Path) -> dict[str, Path]:
    """The pictures and videos the previews play over, made once: each
    named by its recipe, so a different one (the photos installed or
    removed, a new recipe) makes it again."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, args in _recipes().items():
        stem, ext = name.rsplit(".", 1)
        path = folder / f"{stem}-{hashlib.sha256(repr(args).encode()).hexdigest()[:10]}.{ext}"
        if not path.exists():
            partial = path.with_name(f"{path.stem}.partial.{ext}")
            _ffmpeg([*args, str(partial)])
            partial.replace(path)
        paths[stem] = path
    lut = folder / f"teal-orange-{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]}.cube"
    if not lut.exists():
        _teal_orange(lut)
    paths["lut"] = lut
    for path in folder.iterdir():  # samples of older recipes
        if path not in paths.values():
            path.unlink()
    return paths


def _with_own(samples: dict[str, Path], own: Optional[Path]) -> dict[str, Path]:
    """`--with`: the user's still replaces the stills, their video the videos."""
    if own is None:
        return samples
    if own.suffix.lower() in _IMAGE_SUFFIXES:
        return {**samples, "a": own, "portrait": own}
    return {**samples, "video": own, "shaky": own}


# --- previews ----------------------------------------------------------------

def _code_key() -> str:
    """This package's own code and data (the gl-transitions shaders and
    plugin source among them): a change to how anything is built renders
    every preview again. Not the messages or this page's own code, which
    don't change a render (an entry's project is in its own key)."""
    h = hashlib.sha256()
    package = Path(__file__).parent
    for path in sorted([*package.glob("*.py"), *package.glob("*_data/**/*")]):
        if path.is_file() and path.name not in ("i18n.py", "gallery.py"):
            h.update(path.name.encode() + path.read_bytes())
    return h.hexdigest()


def _file_key(path: Path) -> str:
    stat = path.stat()
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _preview_key(entry: Entry, samples: dict[str, Path], base: str) -> str:
    used = sorted(k for k in samples if "{" + k + "}" in entry.project)
    parts = [base, entry.project, *(_file_key(samples[k]) for k in used)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


_RENDER_TIMEOUT_S = 180  # (a filter of melt's may hang on a still)


def _render(entry: Entry, samples: dict[str, Path], work: Path, output: Path) -> Optional[str]:
    """Renders `entry` to `output`; the error, if it didn't."""
    project = entry.project
    for key, path in samples.items():
        project = project.replace("{" + key + "}", str(path))
    # a folder of its own: a derive: removes older generations of its clip
    # from <its YAML's folder>/.derived, and every entry's clip is `d`
    folder = work / output.stem
    folder.mkdir(parents=True, exist_ok=True)
    yaml_path = folder / "project.yaml"
    yaml_path.write_text(project, encoding="utf-8")
    partial = output.with_name(output.stem + ".partial.mp4")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "video_as_code.cli", "render", str(yaml_path), "-o", str(partial), "--no-prune"],
            capture_output=True,
            text=True,
            errors="replace",  # (a filter of melt's may print bytes that aren't UTF-8)
            timeout=_RENDER_TIMEOUT_S,
            cwd=folder,  # (some write files where they run: vidstab its .trf)
        )
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        return msg("gallery.timed_out", seconds=_RENDER_TIMEOUT_S)
    if result.returncode != 0 or not partial.exists():
        partial.unlink(missing_ok=True)
        return (result.stderr or result.stdout).strip() or "melt failed"
    partial.replace(output)
    return None


def _thumbnail(source: Path, output: Path) -> None:
    """A wipe shape's map, stretched to 16:9 as MLT stretches it to the frame."""
    w, h = THUMB_SIZE
    _ffmpeg(["-i", str(source), "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray", "-q:v", "4", str(output)])


def _picture_thumbnail(source: Path, output: Path) -> None:
    """A sample picture the wipe shapes play between, filling the frame."""
    w, h = THUMB_SIZE
    _ffmpeg([
        "-i", str(source), "-frames:v", "1",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", "-q:v", "3", str(output),
    ])


def build(
    own: Optional[Path] = None,
    cache_root: Optional[Path] = None,
    on_progress: Callable[[str], None] = lambda text: None,
) -> Path:
    """Writes the page (rendering what isn't cached yet); returns its path."""
    if probe.find_melt() is None:
        raise KdenliveAsCodeError(msg("cli.render_missing_melt"))
    root = (cache_root or default_cache_root()) / GALLERY_DIR
    files = root / "files"
    work = root / "work"
    files.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    samples = _with_own(_make_samples(root / "samples"), own)
    base = _code_key() + "\n" + str(probe.find_melt())
    melt_base = f"melt catalog 1\n{probe.find_melt()}\n{_melt_version()}"

    def output(e: Entry) -> Path:
        key = _preview_key(e, samples, melt_base if e.melt_catalog else base)
        return files / f"{_slug(e.section)}-{_slug(e.name)}-{key}.mp4"

    melt_catalog = melt_entries(root / "melt-catalog.json")
    if melt_catalog:  # the references "no change" is told against
        melt_catalog = (replace(_SOUND_BASELINE, melt_catalog=True), *melt_catalog)
    catalog = (*CATALOG, *lut_entries(), *gl_entries(), *melt_catalog)
    baselines = {source: replace(e, melt_catalog=True) for source, e in _BASELINES.items()}
    previews = [(e, output(e)) for e in catalog if e.render]
    if melt_catalog:
        previews += [(e, output(e)) for e in baselines.values()]
    baseline_outs = {source: output(e) for source, e in baselines.items()}
    sound_baseline_out = output(replace(_SOUND_BASELINE, melt_catalog=True))
    errors_file = root / "errors.json"
    try:
        errors: dict[str, str] = json.loads(errors_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        errors = {}
    todo = [(e, out) for e, out in previews if not out.exists() and out.name not in errors]
    done = 0

    def run(item: tuple[Entry, Path]) -> None:
        nonlocal done
        entry, out = item
        error = _render(entry, samples, work, out)
        if error:
            errors[out.name] = error
        done += 1
        on_progress(msg("gallery.progress", n=done, total=len(todo), name=entry.name))

    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, todo))

    # which of melt's services show nothing at their defaults
    notes_file = root / "unchanged.json"
    try:
        unchanged: dict[str, bool] = json.loads(notes_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        unchanged = {}
    to_check = [
        (e, out) for e, out in previews
        if e.melt_catalog and e not in baselines.values() and e.name != _SOUND_BASELINE.name and e.section != "melt_analysis"
        and out.exists() and out.name not in unchanged
    ]
    if to_check:
        on_progress(msg("gallery.progress_unchanged", n=len(to_check)))
        with ThreadPoolExecutor(max_workers=8) as pool:
            for (e, out), same in zip(to_check, pool.map(lambda item: _unchanged(*item, baseline_outs, sound_baseline_out), to_check)):
                unchanged[out.name] = same

    # those that change nothing at their defaults: shown with other values,
    # the first that does change something (_tweaks) - or, if none, marked
    tweaks_file = root / "tweaks.json"
    try:
        tweaks: dict[str, Optional[dict]] = json.loads(tweaks_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tweaks = {}
    pending = [
        (e, out) for e, out in previews
        if e.melt_catalog and unchanged.get(out.name) and out.name not in tweaks
    ]
    if pending:
        on_progress(msg("gallery.progress_tweaks", n=len(pending)))

        def tweak(item: tuple[Entry, Path]) -> tuple[str, Optional[dict]]:
            entry, out = item
            for values in _tweaks(entry):
                variant = _with_values(entry, values)
                v_out = output(variant)
                error = None if v_out.exists() else _render(variant, samples, work, v_out)
                if error == msg("gallery.timed_out", seconds=_RENDER_TIMEOUT_S):
                    break  # (one that hangs would hang with every value: not three times)
                if error:
                    continue
                if not _unchanged(variant, v_out, baseline_outs, sound_baseline_out):
                    return out.name, {"values": values, "file": v_out.name}
                v_out.unlink(missing_ok=True)
            return out.name, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            for n, (name, found) in enumerate(pool.map(tweak, pending), start=1):
                tweaks[name] = found
                on_progress(msg("gallery.progress_tweak", n=n, total=len(pending), found=msg("gallery.tweak_found" if found else "gallery.tweak_none")))
    tweaked_files = set()
    for i, (e, out) in enumerate(previews):
        found = tweaks.get(out.name)
        if found:
            previews[i] = (_with_values(e, found["values"]), files / found["file"])
            tweaked_files.add(found["file"])
            unchanged[found["file"]] = False

    # the wipe shapes play from one sample picture to the other
    pictures = []
    for key in ("a", "b"):
        thumb = files / f"sample-{key}-{hashlib.sha256(f'{_file_key(samples[key])}:{THUMB_SIZE}'.encode()).hexdigest()[:16]}.jpg"
        if not thumb.exists():
            _picture_thumbnail(samples[key], thumb)
        pictures.append(thumb)

    luma_cards = []
    to_thumb = []
    for name, source in lumas.all_lumas():
        key = hashlib.sha256(f"{_file_key(source)}:{THUMB_SIZE}".encode()).hexdigest()[:16]
        thumb = files / f"luma-{_slug(name)}-{key}.jpg"
        if not thumb.exists():
            to_thumb.append((source, thumb))
        luma_cards.append((name, thumb))
    if to_thumb:
        on_progress(msg("gallery.progress_lumas", n=len(to_thumb)))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda st: _thumbnail(*st), to_thumb))

    # only what this page uses stays (older generations, a removed entry)
    wanted = (
        {out.name for _, out in previews} | {thumb.name for _, thumb in luma_cards} | {p.name for p in pictures}
        | {output(e).name for e in catalog if e.render}  # (the default renders behind a tweaked one)
    )
    unchanged = {k: v for k, v in unchanged.items() if k in wanted}
    notes_file.write_text(json.dumps(unchanged, indent=1), encoding="utf-8")
    tweaks = {k: v for k, v in tweaks.items() if k in wanted}
    tweaks_file.write_text(json.dumps(tweaks, indent=1), encoding="utf-8")
    for path in files.iterdir():
        if path.name not in wanted:
            path.unlink()
    errors = {k: v for k, v in errors.items() if k in wanted}
    errors_file.write_text(json.dumps(errors, indent=1), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)

    page = root / "index.html"
    shown = [(e, out) for e, out in previews if e not in baselines.values()]
    shown += [(e, None) for e in catalog if not e.render]
    page.write_text(_page(shown, errors, luma_cards, pictures, root, unchanged), encoding="utf-8")
    return page


# --- page --------------------------------------------------------------------

def _page(
    previews: list[tuple[Entry, Path]],
    errors: dict[str, str],
    luma_cards: list[tuple[str, Path]],
    pictures: list[Path],
    root: Path,
    unchanged: Optional[dict[str, bool]] = None,
) -> str:
    def rel(path: Path) -> str:
        return html.escape(path.relative_to(root).as_posix())

    def inline(path: Path) -> str:
        # the page reads these pixels (getImageData), which a page opened
        # from a file may not do with an image it loads from another file
        # (the canvas is "tainted"): so they go in the page itself
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    tab_of = {section: tab for tab, sections_ in TABS.items() for section in sections_}
    sections = []
    for section in SECTIONS:
        if section == "lumas":
            cards = "\n".join(
                f'<figure class="luma"><canvas width="{THUMB_SIZE[0]}" height="{THUMB_SIZE[1]}" data-src="{inline(thumb)}"></canvas>'
                f"<pre><code>transition: wipe\nluma: {html.escape(name)}</code></pre></figure>"
                for name, thumb in luma_cards
            )
            hint = msg("gallery.lumas_hint")
            extra = f' data-from="{inline(pictures[0])}" data-to="{inline(pictures[1])}"'
        else:
            extra = ""
            items = []
            for entry, out in previews:
                if entry.section != section:
                    continue
                classes, badge = [], ""
                if entry.unavailable:
                    classes.append("unavailable")
                    badge = f'<span class="badge">{html.escape(msg("gallery.unavailable_badge"))}</span>'
                if out is None:
                    media = ""
                elif out.name in errors:
                    media = f'<div class="failed"><strong>{html.escape(msg("gallery.failed"))}</strong><pre>{html.escape(errors[out.name])}</pre></div>'
                else:
                    if entry.section == "melt_audio":
                        media = f'<audio src="{rel(out)}" controls preload="none"></audio>'
                    else:
                        media = f'<video src="{rel(out)}" muted loop playsinline preload="none"></video>'
                    if (unchanged or {}).get(out.name):
                        classes.append("unchanged")
                        badge = f'<span class="badge">{html.escape(msg("gallery.unchanged"))}</span>'
                    elif entry.tweaked:
                        badge = f'<span class="badge tweaked">{html.escape(msg("gallery.tweaked_badge"))}</span>'
                about = ""
                if not entry.melt_catalog and not entry.description:
                    key = f"gallery.about.{_slug(entry.name)}"
                    if key in i18n.CATALOGS["en"]:
                        entry = replace(entry, description=msg(key))
                if entry.title or entry.description or entry.tweaked or "unchanged" in classes or entry.unavailable:
                    title = entry.title if entry.title and entry.title.lower() != entry.name.lower() else ""
                    note = ""
                    if entry.unavailable:
                        note = entry.unavailable
                    elif entry.tweaked:
                        note = msg("gallery.tweaked", values=entry.tweaked)
                    elif "unchanged" in classes:
                        tried = _tweaks(entry)
                        if tried:  # (tried, and none changed it)
                            shown_values = "; ".join(", ".join(f"{k}: {json.dumps(v)}" for k, v in t.items()) for t in tried)
                            note = msg("gallery.no_change_found", tried=shown_values)
                        else:  # (nothing it could try)
                            note = msg("gallery.needs_values")
                    text = " - ".join(t for t in (note, title, entry.description) if t)
                    about = f'<p class="about" title="{html.escape(text)}">{html.escape(text)}</p>'
                attrs = f' class="{" ".join(classes)}"' if classes else ""
                if entry.unavailable:  # (why it's grey, on the whole card)
                    attrs += f' title="{html.escape(entry.unavailable)}"'
                items.append(
                    f'<figure{attrs}>{media}<figcaption>{html.escape(entry.name)}{badge}</figcaption>{about}'
                    f"<pre><code>{html.escape(entry.snippet)}</code></pre></figure>"
                )
            cards = "\n".join(items)
            hint = msg(f"gallery.hint_{section}")
            if section == "gl" and not items:
                hint = msg("gallery.hint_gl_not_installed")
        sections.append(
            f'<section id="{section}" data-section="{section}" data-tab="{tab_of[section]}"{extra}><details>'
            f'<summary><h2>{html.escape(msg(f"gallery.section_{section}"))}</h2>'
            f'<span class="count">{cards.count("<figure")}</span></summary>'
            f'<p>{html.escape(hint)}</p><div class="grid">{cards}</div></details></section>'
        )
    nav = "".join(
        f'<a href="#{section}" data-tab="{tab_of[section]}">{html.escape(msg(f"gallery.section_{section}"))}</a>'
        for section in SECTIONS
    )
    tabs = "".join(
        f'<button type="button" role="tab" data-tab="{tab}">{html.escape(msg(f"gallery.tab_{tab}"))}</button>' for tab in TABS
    )
    return (
        _PAGE.replace("{lang}", html.escape(msg("gallery.lang")))
        .replace("{nav}", nav)
        .replace("{tabs}", tabs)
        .replace("{title}", html.escape(msg("gallery.title")))
        .replace("{intro}", html.escape(msg("gallery.intro")))
        .replace("{filter}", html.escape(msg("gallery.filter")))
        .replace("{hide_unchanged}", html.escape(msg("gallery.hide_unchanged")))
        .replace("{copy}", json.dumps(msg("gallery.copy")))
        .replace("{copied}", json.dumps(msg("gallery.copied")))
        .replace("{thumb_w}", str(THUMB_SIZE[0]))
        .replace("{thumb_h}", str(THUMB_SIZE[1]))
        .replace("{sections}", "\n".join(sections))
    )


_PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root { --accent: #f2c14e; --on-accent: #3a2a00; --bg: #f6f5f2; --fg: #22211f; --muted: #6b6862; --card: #ffffff; --line: #dedbd4; --code: #f0eee9; --bad: #b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #181816; --fg: #ecebe7; --muted: #9c9890; --card: #23221f; --line: #34332f; --code: #1c1b19; --bad: #f2b8b5; }
}
body { margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; }
header, section { max-width: 1200px; margin: 0 auto; }
header { position: sticky; top: 0; z-index: 1; background: var(--bg); padding-bottom: 12px; }
h1 { font-size: 20px; margin: 0 0 6px; }
.tabs { display: flex; gap: 4px; margin: 4px 0 12px; border-bottom: 1px solid var(--line); }
.tabs button { font: inherit; font-weight: 600; padding: 8px 16px; border: 1px solid transparent; border-bottom: none;
               border-radius: 8px 8px 0 0; background: none; color: var(--muted); cursor: pointer; margin-bottom: -1px; }
.tabs button[aria-selected="true"] { color: var(--fg); background: var(--bg); border-color: var(--line); }
[data-tab].off { display: none !important; }
nav { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
nav a { padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--card);
        color: var(--fg); text-decoration: none; font-size: 13px; }
nav a:hover { border-color: var(--muted); }
section { scroll-margin-top: 170px; }
summary { cursor: pointer; display: flex; align-items: baseline; gap: 10px; margin: 24px 0 8px; list-style: none; }
summary::-webkit-details-marker { display: none; }
/* a triangle drawn, not a character: the same in every font */
summary::before { content: ""; flex: none; width: 0; height: 0; align-self: center; border-style: solid;
                  border-width: 5px 0 5px 7px; border-color: transparent transparent transparent var(--muted);
                  transition: transform .15s; }
details[open] > summary::before { transform: rotate(90deg); }
summary h2 { margin: 0; }
.count { color: var(--muted); font-size: 13px; }
h2 { font-size: 16px; margin: 32px 0 4px; }
p { margin: 0 0 12px; color: var(--muted); }
input[type="search"] { width: 100%; max-width: 320px; padding: 6px 10px; font: inherit; border: 1px solid var(--line);
        border-radius: 6px; background: var(--card); color: var(--fg); }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px;
         display: flex; flex-direction: column; gap: 6px; min-width: 0; }
video, canvas { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; border-radius: 4px; background: #000; }
figcaption { font-weight: 600; overflow-wrap: anywhere; }
.badge.tweaked { background: var(--accent); color: var(--on-accent); border-color: transparent; font-weight: 600; }
.badge { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 999px; font-weight: 400;
         font-size: 11px; color: var(--muted); border: 1px solid var(--line); }
.about { margin: 0; font-size: 12px; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 3;
         -webkit-box-orient: vertical; overflow: hidden; }
label.hide { display: inline-flex; gap: 6px; align-items: center; margin-left: 12px; color: var(--muted); }
body.hide-unchanged figure.unchanged, body.hide-unchanged figure.unavailable { display: none; }
figure.unavailable { opacity: .5; filter: grayscale(1); }
audio { width: 100%; }
pre { margin: 0; padding: 6px 8px; background: var(--code); border-radius: 4px; overflow-x: auto; cursor: copy;
      font: 12px/1.4 ui-monospace, monospace; }
figure.luma pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.failed { aspect-ratio: 16 / 9; overflow: auto; color: var(--bad); font-size: 12px; }
.failed pre { cursor: auto; white-space: pre-wrap; }
.toast { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); background: var(--fg); color: var(--bg);
         padding: 6px 12px; border-radius: 6px; opacity: 0; transition: opacity .2s; pointer-events: none; }
.toast.on { opacity: 1; }
</style>
</head>
<body class="hide-unchanged">
<header>
<h1>{title}</h1>
<p>{intro}</p>
<div class="tabs" role="tablist">{tabs}</div>
<nav>{nav}</nav>
<input type="search" placeholder="{filter}" aria-label="{filter}">
<label class="hide"><input type="checkbox" checked> {hide_unchanged}</label>
</header>
{sections}
<div class="toast" role="status"></div>
<script>
const COPY = {copy}, COPIED = {copied};
// videos play while on screen
const seen = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting) { e.target.preload = "auto"; e.target.play().catch(() => {}); } else e.target.pause();
  }
});
document.querySelectorAll("video").forEach((v) => seen.observe(v));
// wipe shapes: played here, from one sample picture to the other, as
// MLT's luma does - a pixel changes when the wipe passes its grey level,
// darkest first (see gallery.py)
const lumaSection = document.querySelector('section[data-section="lumas"]');
const W = {thumb_w}, H = {thumb_h}, SOFT = 0.03, CYCLE = 2600, HOLD = 450;
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
function pixels(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = W; c.height = H;
      const ctx = c.getContext("2d"); ctx.drawImage(img, 0, 0, W, H);
      resolve(ctx.getImageData(0, 0, W, H).data);
    };
    img.src = src;
  });
}
function paint(state, pos, from, to) {
  const { ctx, luma, out } = state, d = out.data, p = pos * (1 + SOFT);
  for (let i = 0; i < luma.length; i++) {
    const m = Math.min(1, Math.max(0, (p - luma[i]) / SOFT)), j = i * 4;
    d[j] = from[j] + (to[j] - from[j]) * m; d[j + 1] = from[j + 1] + (to[j + 1] - from[j + 1]) * m;
    d[j + 2] = from[j + 2] + (to[j + 2] - from[j + 2]) * m; d[j + 3] = 255;
  }
  ctx.putImageData(out, 0, 0);
}
function position(t) {  // hold the first picture, wipe, hold the second
  const k = t % CYCLE;
  return Math.min(1, Math.max(0, (k - HOLD) / (CYCLE - 2 * HOLD)));
}
Promise.all([pixels(lumaSection.dataset.from), pixels(lumaSection.dataset.to)]).then(([from, to]) => {
  const playing = new Set(), states = new Map();
  async function load(canvas) {
    const data = await pixels(canvas.dataset.src);
    const luma = new Float32Array(W * H);
    for (let i = 0; i < luma.length; i++) luma[i] = data[i * 4] / 255;
    const ctx = canvas.getContext("2d");
    const state = { ctx, luma, out: ctx.createImageData(W, H) };
    states.set(canvas, state);
    paint(state, 0.5, from, to);
    if (still) {  // reduced motion: only while pointed at
      const fig = canvas.parentElement;
      fig.addEventListener("pointerenter", () => playing.add(canvas));
      fig.addEventListener("pointerleave", () => { playing.delete(canvas); paint(state, 0.5, from, to); });
    } else if (canvas.dataset.visible) playing.add(canvas);
  }
  const seen = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const canvas = e.target;
      if (e.isIntersecting) {
        canvas.dataset.visible = "1";
        if (!states.has(canvas)) load(canvas);
        else if (!still) playing.add(canvas);
      } else { delete canvas.dataset.visible; if (!still) playing.delete(canvas); }
    }
  }, { rootMargin: "100px" });
  lumaSection.querySelectorAll("canvas").forEach((c) => seen.observe(c));
  const frame = (t) => {
    const pos = position(t);
    for (const canvas of playing) if (!canvas.closest("figure").hidden) paint(states.get(canvas), pos, from, to);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
});
// click a snippet to copy it
const toast = document.querySelector(".toast");
for (const pre of document.querySelectorAll("figure > pre")) {
  pre.title = COPY;
  pre.addEventListener("click", () => {
    navigator.clipboard.writeText(pre.textContent).then(() => {
      toast.textContent = COPIED; toast.classList.add("on"); setTimeout(() => toast.classList.remove("on"), 1200);
    }).catch(() => {});
  });
}
// the two tabs; the last one shown is remembered
function showTab(tab) {
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  for (const el of document.querySelectorAll("nav a[data-tab], section[data-tab]")) el.classList.toggle("off", el.dataset.tab !== tab);
  try { localStorage.setItem("gallery-tab", tab); } catch (e) {}
}
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
let firstTab = "transitions";
try { firstTab = localStorage.getItem("gallery-tab") || firstTab; } catch (e) {}
const linked = location.hash && document.querySelector(location.hash + "[data-tab]");
showTab(linked ? linked.dataset.tab : firstTab);
// a link to a section opens it
for (const a of document.querySelectorAll('nav a')) {
  a.addEventListener("click", () => {
    const details = document.querySelector(a.getAttribute("href") + " > details");
    if (details) details.open = true;
  });
}
// each section's count: the cards it shows, after the search and the "hide" box
function shown(fig) {
  return !fig.hidden && !(document.body.classList.contains("hide-unchanged") && fig.matches(".unchanged, .unavailable"));
}
function updateCounts() {
  for (const sec of document.querySelectorAll("section")) {
    const count = sec.querySelector(".count");
    if (count) count.textContent = [...sec.querySelectorAll("figure")].filter(shown).length;
  }
}
document.querySelector("label.hide input").addEventListener("change", (e) => {
  document.body.classList.toggle("hide-unchanged", e.target.checked);
  updateCounts();
});
document.querySelector('input[type="search"]').addEventListener("input", (e) => {
  const q = e.target.value.toLowerCase();
  for (const fig of document.querySelectorAll("figure")) fig.hidden = !fig.textContent.toLowerCase().includes(q);
  for (const sec of document.querySelectorAll("section")) {
    const any = [...sec.querySelectorAll("figure")].some(shown);
    sec.hidden = !any;
    if (q && any) sec.querySelector("details").open = true;  // what a search finds, shown
  }
  updateCounts();
});
updateCounts();
</script>
</body>
</html>
"""
