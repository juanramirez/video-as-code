"""`render --split`: rendering a project in pieces, for machines without
the RAM to render it in one melt run.

melt builds every producer in the file when it loads it, and keeps every
still it has decoded cached until it exits, so a long project with many
full-resolution photos needs gigabytes just to start - more than a
laptop can spare. Rendered here instead:

- the PICTURE in segments, each from a copy of the project trimmed to the
  clips that segment touches (everything else becomes a <blank> of the
  same length, so not a frame moves - trim()), rendered with the same
  in=/out= it would have had in the whole project, then concatenated
  without re-encoding;
- the SOUND in ONE run over the whole length, from a copy trimmed to the
  clips that make sound (trim(audio_only=True)), in parallel with the
  picture, muxed in at the end. Every melt run restarts every audio
  filter, so rendering sound in segments made a time-stretched song
  (`preserve_pitch`) click and warble at every seam.

Segment sizes vary with what each stretch of the timeline holds (plan()):
long where few clips play, short where crossfades pile many up. Each
melt start costs seconds (loading the project, opening its files), which
with fixed 10-frame segments was a large part of the whole render on a
real project; a segment's memory, though, grows with every clip it plays
- estimate_mb(), a deliberately cautious model fitted on measured runs.

Segments are aligned power-of-two blocks (MIN_FRAMES * 2^k frames,
starting at a multiple of their own size), split in halves until each
fits the memory budget. A block's size so depends only on what it
contains, so editing one clip changes the segments around it and no
others - and every segment's render is cached under a key of its trimmed
project and the files it uses (segment_key()), so a re-render only
re-renders what changed.

Each melt runs inside a `systemd-run --user --scope` with a memory cap
when available: if the estimate is wrong and it still runs out, only that
melt dies, not the terminal or the desktop session.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import probe
from .errors import KdenliveAsCodeError
from .i18n import msg

# smallest and largest segment, in frames (largest = MIN_FRAMES * 2^7)
MIN_FRAMES = 10
MAX_FRAMES = MIN_FRAMES * 128
# clips this many frames either side of a segment are kept in its trimmed
# copy too (transitions and fades reaching into it)
MARGIN = 60
CACHE_DIR = ".render_cache"
_SEGMENT_PREFIX = "split_"
_ENCODE = ["vcodec=libx264", "acodec=pcm_s16le"]

# --- memory model -------------------------------------------------------------
# Fitted on peak RSS of melt runs on a real project (a 320x180 preview,
# 156 tracks, ~150 clips; 10- and 300-frame segments): a baseline, plus
# per clip PLAYED in the segment (melt opens every producer at load but
# only decodes, and caches, what it actually plays): ~16-20 bytes per
# pixel of a still (decoded, converted and scaled copies, all kept until
# melt exits) and ~40 per pixel of a video frame (decoder and frame
# buffers), plus a few MB per video. Rounded up: an overestimate only
# makes segments shorter, an underestimate gets melt killed.
_BASE_MB = 150
_STILL_BYTES_PER_PX = 20
_VIDEO_BYTES_PER_PX = 45
_VIDEO_MB = 8
_OTHER_MB = 5
# every track composites a frame of the profile's size
_TRACK_BYTES_PER_PX = 4


class SplitRenderError(KdenliveAsCodeError):
    pass


@dataclass(frozen=True)
class Segment:
    first: int  # frames, inclusive
    last: int
    est_mb: int

    @property
    def frames(self) -> int:
        return self.last - self.first + 1


# --- trimming -----------------------------------------------------------------


def _entry_length(entry: ET.Element, by_id: dict[str, ET.Element]) -> int:
    i, o = entry.get("in"), entry.get("out")
    if i is None or o is None:
        producer = by_id[entry.get("producer")]
        i = i if i is not None else producer.get("in", "0")
        o = o if o is not None else producer.get("out")
    return int(o) - int(i) + 1


def _timeline_entries(root: ET.Element) -> list[tuple[int, int, str]]:
    """(first frame, last frame, producer id) of every playlist entry."""
    by_id = {e.get("id"): e for e in root if e.get("id")}
    entries = []
    for playlist in root.findall("playlist"):
        if playlist.get("id") == "main_bin":
            continue
        pos = 0
        for child in playlist:
            if child.tag == "blank":
                pos += int(child.get("length"))
            elif child.tag == "entry":
                n = _entry_length(child, by_id)
                entries.append((pos, pos + n - 1, child.get("producer")))
                pos += n
    return entries


def trim(root: ET.Element, first: int, last: int, audio_only: bool = False, margin: int = MARGIN) -> ET.Element:
    """A copy of the project keeping only what frames first..last need:
    every playlist entry entirely outside [first - margin, last + margin]
    becomes a <blank> of the same length (so nothing moves and the copy
    renders with the same in=/out=), and producers no longer referenced
    are dropped - from the main_bin playlist too. With `audio_only`, every
    playlist only feeding picture (its track hides audio) is blanked
    whole as well."""
    root = copy.deepcopy(root)
    lo, hi = first - margin, last + margin
    by_id = {e.get("id"): e for e in root if e.get("id")}
    picture_only = (
        {t.get("producer") for tr in root.findall("tractor") for t in tr.findall("track") if t.get("hide") == "audio"}
        if audio_only
        else set()
    )
    for playlist in root.findall("playlist"):
        if playlist.get("id") == "main_bin":
            continue
        blank_all = playlist.get("id") in picture_only
        pos = 0
        for i, child in enumerate(list(playlist)):
            if child.tag == "blank":
                pos += int(child.get("length"))
            elif child.tag == "entry":
                n = _entry_length(child, by_id)
                if blank_all or pos + n - 1 < lo or pos > hi:
                    playlist.remove(child)
                    playlist.insert(i, ET.Element("blank", length=str(n)))
                pos += n

    # an id only the main_bin playlist still references counts as unused
    main_bin = by_id.get("main_bin")
    used: set[str] = set()
    for playlist in root.findall("playlist"):
        if playlist is not main_bin:
            used |= {e.get("producer") for e in playlist.findall("entry")}
    for tractor in root.findall("tractor"):
        used |= {t.get("producer") for t in tractor.findall("track")}
    media = {e.get("id") for e in root if e.tag in ("producer", "chain")}
    drop = {m for m in media if m not in used and m != "producer_black"}
    for e in list(root):
        if e.tag in ("producer", "chain") and e.get("id") in drop:
            root.remove(e)
    if main_bin is not None:
        for e in list(main_bin.findall("entry")):
            if e.get("producer") in drop:
                main_bin.remove(e)
    return root


# --- cache keys ---------------------------------------------------------------


def _resource_path(text: str, base: Path) -> Path:
    # timewarp resources are "<speed>:<path>"
    if not os.path.isabs(text) and ":" in text:
        text = text.split(":", 1)[1]
    return Path(text) if os.path.isabs(text) else base / text


def _renumbered(root: ET.Element) -> ET.Element:
    """Every id renamed in document order: the generator numbers filters
    and producers globally, so adding a fade at the end renumbers the
    music's filters in every segment without changing a frame."""
    root = copy.deepcopy(root)
    ids: dict[str, str] = {}
    for e in root.iter():
        if e.get("id") is not None and e.get("id") not in ids:
            ids[e.get("id")] = f"id{len(ids)}"
    for e in root.iter():
        for attr in ("id", "producer"):
            if e.get(attr) in ids:
                e.set(attr, ids[e.get(attr)])
        if e.tag == "property" and e.text in ids:
            e.text = ids[e.text]
    return root


def _sound_items(root: ET.Element) -> list[bytes]:
    """One canonical string per sounding entry: absolute start, entry and
    producer, without ids or kdenlive:* bookkeeping properties."""
    by_id = {e.get("id"): e for e in root if e.get("id")}

    def canon(element: ET.Element) -> bytes:
        element = copy.deepcopy(element)
        for x in element.iter():
            x.attrib.pop("id", None)
            x.attrib.pop("producer", None)  # the producer itself is hashed right after its entry
            for c in [c for c in x if c.tag == "property" and (c.get("name") or "").startswith("kdenlive:")]:
                x.remove(c)
        return ET.tostring(element)

    items = []
    for playlist in root.findall("playlist"):
        if playlist.get("id") == "main_bin":
            continue
        pos = 0
        for child in playlist:
            if child.tag == "blank":
                pos += int(child.get("length"))
            elif child.tag == "entry":
                producer = by_id[child.get("producer")]
                n = _entry_length(child, by_id) if producer.tag != "tractor" else (
                    int(child.get("out", producer.get("out"))) - int(child.get("in", producer.get("in", "0"))) + 1
                )
                if producer.tag != "tractor":
                    items.append(b"%d\0" % pos + canon(child) + b"\0" + canon(producer))
                pos += n
    return sorted(items)  # track order doesn't matter for the summed sound


def segment_key(root: ET.Element, base: Path, extras: list[str], audio: bool = False) -> str:
    """Cache key of a trimmed segment: its XML (ids renumbered), the size
    and mtime of every file it reads (replacing a file under the same name
    invalidates it) and `extras` (frame range, encoder settings).

    For the one-pass sound (`audio`), the XML isn't hashed whole - picture
    edits change it (the lengths of the blanks left on picture tracks)
    without changing a sample - but every sounding entry (_sound_items)."""
    h = hashlib.sha256()
    if audio:
        # (on the original ids: _sound_items leaves them out anyway, and
        # needs to tell the main_bin playlist apart)
        profile = root.find("profile")
        if profile is not None:
            h.update(ET.tostring(profile))
        for item in _sound_items(root):
            h.update(b"\0" + item)
    else:
        h.update(ET.tostring(_renumbered(root)))
    for extra in extras:
        h.update(b"\0" + extra.encode())
    files = set()
    for prop in root.iter("property"):
        if prop.get("name") == "resource" and prop.text:
            path = _resource_path(prop.text, base)
            if path.is_file():
                files.add(path)
    for path in sorted(files):
        st = path.stat()
        h.update(f"\0{path}\0{st.st_size}\0{st.st_mtime_ns}".encode())
    return h.hexdigest()


# --- planning -----------------------------------------------------------------


def _frame_pixels(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip().split(",")
    try:
        return int(out[0]) * int(out[1])
    except (ValueError, IndexError):
        return 1920 * 1080  # unknown: assume a full-HD frame


def producer_costs(root: ET.Element, base: Path) -> dict[str, float]:
    """Estimated MB each producer adds once melt plays it."""
    costs: dict[str, float] = {}
    pixels: dict[Path, int] = {}
    for e in root:
        if e.tag not in ("producer", "chain"):
            continue
        props = {p.get("name"): p.text for p in e.findall("property")}
        service = props.get("mlt_service") or ""
        resource = props.get("resource")
        if service in ("qimage", "pixbuf", "avformat", "avformat-novalidate", "timewarp") and resource:
            path = _resource_path(resource, base)
            if path not in pixels:
                pixels[path] = _frame_pixels(path) if path.is_file() else 0
            px = pixels[path]
            if service in ("qimage", "pixbuf"):
                costs[e.get("id")] = px * _STILL_BYTES_PER_PX / 1e6
            else:
                costs[e.get("id")] = _VIDEO_MB + px * _VIDEO_BYTES_PER_PX / 1e6
        else:
            costs[e.get("id")] = _OTHER_MB
    return costs


def baseline_mb(root: ET.Element) -> float:
    profile = root.find("profile")
    px = int(profile.get("width", 1920)) * int(profile.get("height", 1080)) if profile is not None else 1920 * 1080
    tracks = sum(len(t.findall("track")) for t in root.findall("tractor"))
    return _BASE_MB + tracks * px * _TRACK_BYTES_PER_PX / 1e6


def plan(
    total_frames: int,
    entries: list[tuple[int, int, str]],
    costs: dict[str, float],
    base_mb: float,
    budget_mb: float,
) -> list[Segment]:
    """Segments covering frames 0..total_frames-1: aligned blocks of
    MAX_FRAMES, each halved (recursively) until its estimate - base_mb
    plus the cost of every producer played in it - fits budget_mb, or it
    is MIN_FRAMES long (rendered anyway, the best that can be done)."""

    def estimate(first: int, last: int) -> float:
        played = {pid for start, end, pid in entries if start <= last and end >= first}
        return base_mb + sum(costs.get(pid, 0.0) for pid in played)

    segments: list[Segment] = []

    def split(first: int, size: int) -> None:
        last = min(first + size - 1, total_frames - 1)
        if first > last:
            return
        est = estimate(first, last)
        if est <= budget_mb or size <= MIN_FRAMES:
            segments.append(Segment(first, last, round(est)))
            return
        split(first, size // 2)
        split(first + size // 2, size // 2)

    for start in range(0, total_frames, MAX_FRAMES):
        split(start, MAX_FRAMES)
    return segments


def default_budget_mb() -> int:
    """60% of the memory available right now (minus room for the sound
    pass running alongside), at least 600 MB; 2 GB where unknown."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            info = {line.split(":")[0]: line.split()[1] for line in fh}
        available_mb = int(info["MemAvailable"]) / 1024
    except (OSError, KeyError, ValueError):
        return 2048
    return max(600, int(available_mb * 0.6) - 200)


# --- running melt -------------------------------------------------------------


def _scope_available() -> bool:
    if os.environ.get("VIDEO_AS_CODE_NO_SCOPE") or not shutil.which("systemd-run"):
        return False
    try:
        return (
            subprocess.run(
                ["systemd-run", "--user", "--scope", "-q", "true"], capture_output=True, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _melt_command(melt: str, args: list[str], cap_mb: Optional[int]) -> list[str]:
    command = [melt, *args]
    if cap_mb is not None:
        command = ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={cap_mb}M", "--", *command]
    return command


# Where a running render publishes how far it got, for anything that wants
# to show it (a progress bar in another window, say): one JSON file per
# user, the latest render's - {output, total, done, segment, segments,
# phase (render/audio/join/done/failed), started, updated (epoch seconds),
# pid, utc_offset (seconds)}. Written at most once a second, atomically.
PROGRESS_FILE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "video-as-code" / "render-progress.json"
_CURRENT_FRAME = re.compile(rb"Current Frame:\s*(\d+)")


class Progress:
    def __init__(self, output: Path, total_frames: int, path: Optional[Path] = None) -> None:
        self.path = path or PROGRESS_FILE
        self.state = {"output": str(Path(output).resolve()), "total": total_frames, "done": 0, "segment": 0,
                      "segments": 0, "phase": "render", "started": time.time(), "pid": os.getpid(),
                      # so a reader can show clock times in the renderer's own time zone
                      "utc_offset": time.localtime().tm_gmtoff}
        self._last_write = 0.0
        self._write(force=True)

    def update(self, done: int, segment: int, segments: int) -> None:
        self.state.update(done=done, segment=segment, segments=segments)
        self._write()

    def phase(self, name: str) -> None:
        self.state["phase"] = name
        self._write(force=True)

    def finish(self, phase: str = "done") -> None:
        if phase == "done":
            self.state["done"] = self.state["total"]
        self.phase(phase)

    def _write(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_write < 1:
            return
        self._last_write = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({**self.state, "updated": now}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # showing progress is a nicety: never fail a render over it


def _run_melt_with_progress(cmd: list[str], env: dict[str, str], on_frame: Callable[[int], None]) -> tuple[int, str]:
    """Runs melt, passing each frame it reports ("Current Frame: N", on
    stderr, \r-separated) to `on_frame`; returns (exit code, stderr's tail)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    tail = b""
    assert proc.stderr is not None
    while chunk := proc.stderr.read1(65536):
        tail = (tail + chunk)[-8192:]
        frames = _CURRENT_FRAME.findall(chunk)
        if frames:
            on_frame(int(frames[-1]))
    return proc.wait(), tail.decode(errors="replace")


def render(
    kdenlive_path: Path,
    output: Path,
    total_frames: int,
    melt: str,
    cache_dir: Path,
    budget_mb: Optional[int] = None,
    report: Callable[[str], None] = lambda text: None,
    memory_cap: Optional[bool] = None,
) -> list[Segment]:
    """Renders `kdenlive_path` to `output` in segments (see the module
    docstring); returns the plan used. Raises SplitRenderError."""
    root = ET.parse(kdenlive_path).getroot()
    base = Path(root.get("root") or kdenlive_path.parent)
    budget = budget_mb or default_budget_mb()
    segments = plan(total_frames, _timeline_entries(root), producer_costs(root, base), baseline_mb(root), budget)
    over = [s for s in segments if s.est_mb > budget]
    if over:
        report(msg("split.over_budget", n=len(over), budget=budget))
    report(msg("split.plan", n=len(segments), frames=total_frames, budget=budget))
    use_scope = _scope_available() if memory_cap is None else memory_cap
    # the cap leaves room for the estimate being low; it's there so that
    # running out kills melt, not the session
    cap = (lambda est: max(int(est * 1.5), est + 512)) if use_scope else (lambda est: None)
    env = probe.melt_env()
    cache_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(output, total_frames)

    with tempfile.TemporaryDirectory(dir=cache_dir) as tmp_name:
        tmp = Path(tmp_name)
        # sound: one run over the whole length, alongside the picture
        audio_root = trim(root, 0, total_frames - 1, audio_only=True)
        audio = cache_dir / f"{_SEGMENT_PREFIX}audio.wav"
        audio_key = segment_key(audio_root, base, [f"0-{total_frames - 1}", "audio pcm_s16le"], audio=True)
        audio_proc = None
        if audio.exists() and _read(audio.with_suffix(".key")) == audio_key:
            report(msg("split.audio_cached"))
        else:
            report(msg("split.audio_start"))
            audio_xml = tmp / "audio.kdenlive"
            ET.ElementTree(audio_root).write(audio_xml, encoding="utf-8", xml_declaration=True)
            audio_log = (tmp / "audio.log").open("wb")
            audio_proc = subprocess.Popen(
                _melt_command(
                    melt,
                    [str(audio_xml), "-consumer", f"avformat:{tmp / 'audio.wav'}", "vn=1", "acodec=pcm_s16le",
                     "in=0", f"out={total_frames - 1}"],
                    cap(1024),
                ),
                stdout=audio_log, stderr=subprocess.STDOUT, env=env,
            )

        parts = []
        try:
            for n, seg in enumerate(segments, start=1):
                part = cache_dir / f"{_SEGMENT_PREFIX}{seg.first:06d}_{seg.last:06d}.mkv"
                seg_root = trim(root, seg.first, seg.last)
                key = segment_key(seg_root, base, [f"{seg.first}-{seg.last}", " ".join(_ENCODE)])
                parts.append(part)
                done_before = sum(s.frames for s in segments[:n - 1])
                if part.exists() and _read(part.with_suffix(".key")) == key:
                    report(msg("split.segment_cached", n=n, total=len(segments), first=seg.first, last=seg.last))
                    progress.update(done_before + seg.frames, n, len(segments))
                    continue
                progress.update(done_before, n, len(segments))
                report(msg("split.segment", n=n, total=len(segments), first=seg.first, last=seg.last, mb=seg.est_mb))
                seg_xml = tmp / "segment.kdenlive"
                ET.ElementTree(seg_root).write(seg_xml, encoding="utf-8", xml_declaration=True)
                rendered = tmp / part.name
                returncode, stderr = _run_melt_with_progress(
                    _melt_command(
                        melt,
                        [str(seg_xml), "-consumer", f"avformat:{rendered}", *_ENCODE, f"in={seg.first}", f"out={seg.last}"],
                        cap(seg.est_mb),
                    ),
                    env,
                    lambda frame: progress.update(done_before + max(0, min(frame - seg.first, seg.frames)), n, len(segments)),
                )
                if returncode != 0 or not rendered.exists():
                    detail = (stderr.strip().splitlines() or [f"exit {returncode}"])[-1]
                    raise SplitRenderError(msg("split.segment_failed", first=seg.first, last=seg.last, detail=detail))
                # only a finished segment enters the cache
                shutil.move(str(rendered), part)
                part.with_suffix(".key").write_text(key, encoding="utf-8")
        except BaseException:
            if audio_proc is not None:
                audio_proc.kill()
            progress.finish("failed")
            raise

        if audio_proc is not None:
            progress.phase("audio")
            report(msg("split.audio_wait"))
            if audio_proc.wait() != 0 or not (tmp / "audio.wav").exists():
                detail = (tmp / "audio.log").read_text(errors="replace").strip()[-300:]
                progress.finish("failed")
                raise SplitRenderError(msg("split.audio_failed", detail=detail))
            shutil.move(str(tmp / "audio.wav"), audio)
            audio.with_suffix(".key").write_text(audio_key, encoding="utf-8")

        # segments of an earlier plan (the project changed, or the budget)
        wanted = {p.name for p in parts} | {p.with_suffix(".key").name for p in parts} | {audio.name, audio.with_suffix(".key").name}
        for old in cache_dir.glob(f"{_SEGMENT_PREFIX}*"):
            if old.name not in wanted and old.is_file():
                old.unlink()

        progress.phase("join")
        report(msg("split.concat"))
        concat_list = tmp / "concat.txt"
        # absolute paths: the concat demuxer resolves relative ones against the list's own directory
        concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
        # picture from the segments (their own sound ignored), sound from the one-pass render
        run = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-i", str(audio),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
             "-movflags", "+faststart", str(output)],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            progress.finish("failed")
            raise SplitRenderError(msg("split.concat_failed", detail=run.stderr.strip()[-300:]))
    progress.finish()
    return segments


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
