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

from . import gltransitions, lumas, probe
from .errors import KdenliveAsCodeError
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
    project = _HEADER + f"""bin:
  - {{id: a, path: "{{a}}"}}
  - {{id: b, path: "{{b}}"}}
  - {{id: video, path: "{{video}}"}}
  - {{id: shaky, path: "{{shaky}}"}}
  - {{id: green, path: "{{green}}"}}
timeline:
  tracks:
{below}    - type: video
      clips:
        - clip: {source}
          start: 0
{_indent(keys, 10)}
"""
    if "duration:" not in keys and "out:" not in keys:
        project += "          duration: 3\n"
    return Entry("clip", name, keys.strip(), project)


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
    _title("title card", 'text: "Hello"\nfont_size: 40\nbackground: [0, 0, 0, 120]'),
)

SECTIONS = ("transitions", "lumas", "gl", "clip", "derive", "title")


def gl_entries() -> tuple[Entry, ...]:
    """One per gl-transitions transition, with its defaults - once its
    plugin is built (gltransitions.py); none before."""
    if not gltransitions.installed():
        return ()
    return tuple(_scene(name, f"transition: gl\ngl: {name}", section="gl") for name in gltransitions.catalog())


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
    result = subprocess.run(
        [sys.executable, "-m", "video_as_code.cli", "render", str(yaml_path), "-o", str(partial), "--no-prune"],
        capture_output=True,
        text=True,
    )
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

    previews = [(e, files / f"{_slug(e.section)}-{_slug(e.name)}-{_preview_key(e, samples, base)}.mp4") for e in (*CATALOG, *gl_entries())]
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
    wanted = {out.name for _, out in previews} | {thumb.name for _, thumb in luma_cards} | {p.name for p in pictures}
    for path in files.iterdir():
        if path.name not in wanted:
            path.unlink()
    errors = {k: v for k, v in errors.items() if k in wanted}
    errors_file.write_text(json.dumps(errors, indent=1), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)

    page = root / "index.html"
    page.write_text(_page(previews, errors, luma_cards, pictures, root), encoding="utf-8")
    return page


# --- page --------------------------------------------------------------------

def _page(
    previews: list[tuple[Entry, Path]],
    errors: dict[str, str],
    luma_cards: list[tuple[str, Path]],
    pictures: list[Path],
    root: Path,
) -> str:
    def rel(path: Path) -> str:
        return html.escape(path.relative_to(root).as_posix())

    def inline(path: Path) -> str:
        # the page reads these pixels (getImageData), which a page opened
        # from a file may not do with an image it loads from another file
        # (the canvas is "tainted"): so they go in the page itself
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    sections = []
    for section in SECTIONS:
        if section == "lumas":
            cards = "\n".join(
                f'<figure class="luma"><canvas width="{THUMB_SIZE[0]}" height="{THUMB_SIZE[1]}" data-src="{inline(thumb)}"></canvas>'
                f"<pre><code>luma: {html.escape(name)}</code></pre></figure>"
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
                if out.name in errors:
                    media = f'<div class="failed"><strong>{html.escape(msg("gallery.failed"))}</strong><pre>{html.escape(errors[out.name])}</pre></div>'
                else:
                    media = f'<video src="{rel(out)}" muted loop playsinline preload="none"></video>'
                items.append(
                    f'<figure>{media}<figcaption>{html.escape(entry.name)}</figcaption>'
                    f"<pre><code>{html.escape(entry.snippet)}</code></pre></figure>"
                )
            cards = "\n".join(items)
            hint = msg(f"gallery.hint_{section}")
            if section == "gl" and not items:
                hint = msg("gallery.hint_gl_not_installed")
        sections.append(
            f'<section id="{section}" data-section="{section}"{extra}><h2>{html.escape(msg(f"gallery.section_{section}"))}</h2>'
            f'<p>{html.escape(hint)}</p><div class="grid">{cards}</div></section>'
        )
    nav = "".join(f'<a href="#{section}">{html.escape(msg(f"gallery.section_{section}"))}</a>' for section in SECTIONS)
    return (
        _PAGE.replace("{lang}", html.escape(msg("gallery.lang")))
        .replace("{nav}", nav)
        .replace("{title}", html.escape(msg("gallery.title")))
        .replace("{intro}", html.escape(msg("gallery.intro")))
        .replace("{filter}", html.escape(msg("gallery.filter")))
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
:root { --bg: #f6f5f2; --fg: #22211f; --muted: #6b6862; --card: #ffffff; --line: #dedbd4; --code: #f0eee9; --bad: #b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #181816; --fg: #ecebe7; --muted: #9c9890; --card: #23221f; --line: #34332f; --code: #1c1b19; --bad: #f2b8b5; }
}
body { margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; }
header, section { max-width: 1200px; margin: 0 auto; }
header { position: sticky; top: 0; z-index: 1; background: var(--bg); padding-bottom: 12px; }
h1 { font-size: 20px; margin: 0 0 6px; }
nav { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
nav a { padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--card);
        color: var(--fg); text-decoration: none; font-size: 13px; }
nav a:hover { border-color: var(--muted); }
section { scroll-margin-top: 170px; }
h2 { font-size: 16px; margin: 32px 0 4px; }
p { margin: 0 0 12px; color: var(--muted); }
input { width: 100%; max-width: 320px; padding: 6px 10px; font: inherit; border: 1px solid var(--line);
        border-radius: 6px; background: var(--card); color: var(--fg); }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px;
         display: flex; flex-direction: column; gap: 6px; min-width: 0; }
video, canvas { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; border-radius: 4px; background: #000; }
figcaption { font-weight: 600; }
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
<body>
<header>
<h1>{title}</h1>
<p>{intro}</p>
<nav>{nav}</nav>
<input type="search" placeholder="{filter}" aria-label="{filter}">
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
document.querySelector("input").addEventListener("input", (e) => {
  const q = e.target.value.toLowerCase();
  for (const fig of document.querySelectorAll("figure")) fig.hidden = !fig.textContent.toLowerCase().includes(q);
  for (const sec of document.querySelectorAll("section"))
    sec.hidden = ![...sec.querySelectorAll("figure")].some((f) => !f.hidden);
});
</script>
</body>
</html>
"""
