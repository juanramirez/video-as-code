"""`video-as-code watch`: the project rendered again each time its YAML
is saved, and played from where it changed.

Each save renders a light preview (`--proxy`, 480x270 copies by default)
the way `render --split` renders: in segments, each cached under a key of
what it plays (splitrender.segment_key), so an edit re-renders only the
segments it touches - a few seconds of video, not the whole of it. The
segments are short here (at most WATCH_SEGMENT_S), unlike a full
render's, which are as long as memory allows: shorter ones cost a melt
start each, but an edit then re-renders little. Where in a re-rendered
segment the picture changed is found by its frames' hashes (ffmpeg's
framemd5) against the last render's of it, kept next to it: the player
opens the preview LEAD_IN_S before the first frame that differs.

The cache is its own (<project dir>/.render_cache/watch/), so a preview
never replaces a full render's segments, nor the other way round.

What's watched: every .yaml/.yml under the project file's folder (its
parts, a presets file - not those in hidden folders like .derived/).
A save that doesn't validate is reported and the last preview stays.

The player: VIDEO_AS_CODE_PLAYER if set - a command, with `{file}` and
`{start}` (seconds) in it - else the first of mpv, vlc and ffplay found;
a new one replaces the last each time.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import probe, splitrender, xmlgen
from .errors import KdenliveAsCodeError
from .i18n import msg
from .schema import load_and_resolve

POLL_S = 0.5
LEAD_IN_S = 1.0
WATCH_SEGMENT_S = 8.0
WATCH_DIR = "watch"  # under splitrender.CACHE_DIR
DEFAULT_PROXY = "480x270"
PLAYER_ENV = "VIDEO_AS_CODE_PLAYER"
PLAYERS = {
    "mpv": ["mpv", "--start={start}", "--keep-open=yes", "{file}"],
    "vlc": ["vlc", "--start-time={start}", "{file}"],
    "ffplay": ["ffplay", "-ss", "{start}", "{file}"],
}


@dataclass
class Preview:
    output: Path
    fps: float
    changed: Optional[tuple[int, int]]  # first and last frame that differ from the last render (None: none)

    def start_s(self) -> Optional[float]:
        """Where to play from: a little before the first frame that changed."""
        if self.changed is None:
            return None
        return max(0.0, self.changed[0] / self.fps - LEAD_IN_S)


def segment_frames(fps: float) -> int:
    """The longest segment: the largest MIN_FRAMES * 2^k within WATCH_SEGMENT_S."""
    frames = splitrender.MIN_FRAMES
    while frames * 2 <= WATCH_SEGMENT_S * fps and frames * 2 <= splitrender.MAX_FRAMES:
        frames *= 2
    return frames


def watched(yaml_path: Path) -> dict[Path, tuple[int, int]]:
    """Every YAML file a save of which re-renders: path -> (mtime, size)."""
    root = yaml_path.resolve().parent
    files = {yaml_path.resolve()}
    for pattern in ("*.yaml", "*.yml"):
        files |= {p for p in root.rglob(pattern) if not any(part.startswith(".") for part in p.relative_to(root).parts)}
    out = {}
    for path in sorted(files):
        try:
            st = path.stat()
        except OSError:  # (gone between listing and stat: an editor's swap file)
            continue
        out[path] = (st.st_mtime_ns, st.st_size)
    return out


def cache_dir(yaml_path: Path, part: Optional[str]) -> Path:
    name = part.strip("/").replace("/", "-") if part else yaml_path.stem
    return yaml_path.resolve().parent / splitrender.CACHE_DIR / WATCH_DIR / name


def render_once(yaml_path: Path, part: Optional[str], proxy: Optional[str], report: Callable[[str], None]) -> Optional[Preview]:
    """The preview, rendered again (what changed only). None if it
    couldn't be, after saying why; KdenliveAsCodeError if the project
    doesn't validate."""
    from .cli import _with_proxies, melt_env_for  # (cli imports this module)

    resolved = load_and_resolve(yaml_path, part=part)
    melt = probe.find_melt()
    if melt is None:
        raise KdenliveAsCodeError(msg("cli.render_missing_melt"))
    resolved = _with_proxies(resolved, proxy)
    if resolved is None:
        return None
    env = melt_env_for(resolved, melt)
    if env is None:
        return None
    folder = cache_dir(yaml_path, part)
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "preview.mp4"
    # written apart and moved in: a player still showing the last one
    # keeps reading its own file
    partial = folder / "preview.partial.mp4"
    rendered: list[splitrender.Segment] = []
    with tempfile.TemporaryDirectory() as tmp:
        kdenlive_path = Path(tmp) / f"{yaml_path.stem}.kdenlive"
        xmlgen.write(resolved, kdenlive_path)
        splitrender.render(kdenlive_path, partial, resolved.total_frames, melt, folder, report=report, env=env,
                           max_frames=segment_frames(resolved.fps.fps), rendered=rendered, report_cached=False)
    os.replace(partial, output)
    return Preview(output, resolved.fps.fps, changed_frames(folder, rendered))


def frame_hashes(video: Path) -> list[str]:
    """Each frame's md5, in order (ffmpeg's framemd5)."""
    run = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v", "-f", "framemd5", "-"],
                         capture_output=True, text=True)
    return [line.rsplit(",", 1)[1].strip() for line in run.stdout.splitlines() if line.strip() and not line.startswith("#")]


def _hashes_file(folder: Path, segment: splitrender.Segment) -> Path:
    # (not split_*: splitrender removes what of that name its plan doesn't have)
    return folder / f"frames_{segment.first:06d}_{segment.last:06d}.txt"


def changed_frames(folder: Path, rendered: list[splitrender.Segment]) -> Optional[tuple[int, int]]:
    """The first and last frame, of the `rendered` segments, whose picture
    differs from the last render of that segment (all of a segment with
    none) - and each one's hashes kept for the next time."""
    first = last = None
    for seg in rendered:
        video = folder / f"{splitrender._SEGMENT_PREFIX}{seg.first:06d}_{seg.last:06d}.mkv"
        new = frame_hashes(video)
        kept = _hashes_file(folder, seg)
        old = kept.read_text(encoding="utf-8").split() if kept.is_file() else []
        kept.write_text("\n".join(new) + "\n", encoding="utf-8")
        differ = [i for i in range(max(len(new), len(old))) if i >= len(new) or i >= len(old) or new[i] != old[i]]
        if differ:
            a, b = seg.first + differ[0], min(seg.first + differ[-1], seg.last)
            first, last = (a, b) if first is None else (min(first, a), max(last, b))
    return None if first is None else (first, last)


def player_command(file: Path, start: float) -> Optional[list[str]]:
    template = os.environ.get(PLAYER_ENV)
    if template:
        args = shlex.split(template)
    else:
        found = next((name for name in PLAYERS if shutil.which(name)), None)
        if found is None:
            return None
        args = PLAYERS[found]
    values = {"file": str(file), "start": f"{start:.2f}"}
    return [a.format(**values) for a in args]


def clock(seconds: float) -> str:
    """1:23.4"""
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:04.1f}"


class Player:
    """The player showing the preview: a new one for each, the last closed."""

    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None

    def show(self, file: Path, start: float, report: Callable[[str], None]) -> None:
        self.close()
        cmd = player_command(file, start)
        if cmd is None:
            report(msg("watch.no_player", file=file, start=clock(start), env=PLAYER_ENV))
            return
        self.process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


def run(
    yaml_path: Path,
    part: Optional[str] = None,
    proxy: Optional[str] = DEFAULT_PROXY,
    play: bool = True,
    report: Callable[[str], None] = lambda text: None,
    poll_s: float = POLL_S,
    renders: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
    player: Optional[Player] = None,
) -> None:
    """Renders the preview, then again on every save, until interrupted
    (or after `renders` of them, for the tests)."""
    player = player or Player()
    seen: Optional[dict] = None
    last_start = 0.0
    report(msg("watch.watching", path=yaml_path))
    while renders is None or renders > 0:
        now = watched(yaml_path)
        if now == seen:
            sleep(poll_s)
            continue
        if seen is not None:
            sleep(poll_s)  # (an editor may save in steps: let it finish)
            if watched(yaml_path) != now:
                continue
        seen = now
        try:
            preview = render_once(yaml_path, part, proxy, report)
        except KdenliveAsCodeError as exc:
            report(str(exc))
            report(msg("watch.kept"))
            preview = None
        if renders is not None:
            renders -= 1
        if preview is None:
            continue
        start = preview.start_s()
        if start is None:
            report(msg("watch.picture_unchanged", file=preview.output, start=clock(last_start)))
        else:
            last = preview.changed[1] + 1
            report(msg("watch.rendered", file=preview.output, first=clock(preview.changed[0] / preview.fps),
                       last=clock(last / preview.fps), start=clock(start)))
            last_start = start
        if play:
            player.show(preview.output, last_start, report)
        report(msg("watch.waiting"))
