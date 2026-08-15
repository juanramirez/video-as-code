"""Reconstructs a `kdgen` YAML from an already-rendered video plus a folder
of the source assets that presumably went into it — not from a `.kdenlive`
project file (see `reverse.py` for that). There is no structure to parse
here, just pixels and audio samples, so this works by content-based
matching instead of XML parsing: unlike `reverse.py`, there is no
guaranteed correct answer.

Requires the `reconstruct` extra (`numpy`, `Pillow`) — imported lazily by
callers (see `cli.py`) so the base install stays light.

Frame signatures are raw downsampled RGB values compared by Euclidean
distance, not a binarized perceptual hash (textbook average-hash collapses
on flat/solid-color frames: every pixel equals the frame's own mean, so
every solid color hashes identically regardless of hue — found while
prototyping this). Audio matching is FFT-based cross-correlation of
downsampled waveforms, implemented directly on `numpy.fft` rather than
adding a `scipy` dependency for a single function.

Known limitation: matching compares against each asset's *plain* content,
so a segment that's actually mid-fade (pixels darkened/audio quieted
relative to the source) can score too poorly to match confidently, and
scene-cut segmentation can split a fade region into extra spurious
segments (a fade is itself a run of changing frames). Both fail safe -
they land in `x_reconstruct_unmatched` instead of a wrong guess - but a
fade at a segment boundary is more likely to need manual review than a
plain cut. Not yet addressed: segmentation/matching that account for a
possible in-progress fade rather than assuming plain content throughout.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from . import probe
from .i18n import msg
from .timecode import resolve_fps

SIGNATURE_SIZE = 8  # frame signatures are SIGNATURE_SIZE x SIGNATURE_SIZE x 3 floats
COARSE_SAMPLE_INTERVAL = 0.2  # seconds, for identifying which asset a segment came from
AUDIO_SAMPLE_RATE = 8000  # Hz, downsampled for cross-correlation

# Confidence cutoffs below which a match isn't trusted and gets flagged
# instead of guessed. Seeded from prototype/test measurements: real video
# matches stayed well under 500 (near-zero for a clean match, low hundreds
# under heavy recompression) while distractors scored 1000+; real audio
# matches stayed above 0.7 (even for an unusually hard periodic test tone)
# while distractors scored ~0.0. Deliberately conservative gaps, not tight
# to the observed numbers - tune down as more real-world cases come in.
VIDEO_MATCH_DISTANCE_THRESHOLD = 500.0
AUDIO_MATCH_SCORE_THRESHOLD = 0.3

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}


class ReconstructError(RuntimeError):
    pass


# -- low-level media extraction ----------------------------------------------


def _run_ffmpeg(args: list[str], timeout: int = 60) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ReconstructError(msg("reconstruct.ffmpeg_failed", stderr=result.stderr.strip()))


def _signature_from_image(img: Image.Image) -> np.ndarray:
    small = img.convert("RGB").resize((SIGNATURE_SIZE, SIGNATURE_SIZE), Image.LANCZOS)
    return np.asarray(small, dtype=np.float64).flatten()


def extract_frame_signatures(
    path: Path,
    tmp_dir: Path,
    *,
    fps: float,
    start_s: float = 0.0,
    duration_s: Optional[float] = None,
) -> list[np.ndarray]:
    """Bulk-extracts frames at `fps` (optionally just a `[start_s, start_s +
    duration_s)` window) and returns one signature per frame, in order.
    Bulk extraction (one ffmpeg call, `-vf fps=`) instead of one call per
    timestamp — much faster for anything beyond a handful of frames."""
    out_dir = tmp_dir / f"frames_{path.stem}_{fps}_{start_s}_{duration_s}"
    out_dir.mkdir(parents=True, exist_ok=True)
    args = []
    if start_s > 0:
        args += ["-ss", str(start_s)]
    args += ["-i", str(path)]
    if duration_s is not None:
        args += ["-t", str(duration_s)]
    args += ["-vf", f"fps={fps}", str(out_dir / "f_%06d.png")]
    _run_ffmpeg(args)
    signatures = []
    for png in sorted(out_dir.glob("f_*.png")):
        with Image.open(png) as img:
            signatures.append(_signature_from_image(img))
    return signatures


def probe_video_info(path: Path) -> dict:
    """width/height/fps of a video's first stream, via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise ReconstructError(msg("reconstruct.no_video_stream", path=path))
    stream = streams[0]
    num, den = (int(x) for x in stream["r_frame_rate"].split("/"))
    return {"width": int(stream["width"]), "height": int(stream["height"]), "fps": num / den}


def extract_image_signature(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return _signature_from_image(img)


def extract_audio_waveform(path: Path, tmp_dir: Path, *, sample_rate: int = AUDIO_SAMPLE_RATE) -> np.ndarray:
    """Mono, 16-bit PCM waveform as float64 samples. Uses the stdlib `wave`
    module to read it back instead of a third-party WAV reader — ffmpeg
    already guarantees the format via the encoding flags below."""
    wav_path = tmp_dir / f"audio_{path.stem}.wav"
    _run_ffmpeg(
        ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-acodec", "pcm_s16le", str(wav_path)]
    )
    with wave.open(str(wav_path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        sample_width = wf.getsampwidth()
    if sample_width != 2:
        raise ReconstructError(msg("reconstruct.unexpected_wav_sample_width", width=sample_width))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


# -- asset signature bank -----------------------------------------------------


def guess_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "unknown"


def asset_files(assets_dir: Path) -> list[Path]:
    return sorted(p for p in assets_dir.iterdir() if p.is_file() and guess_kind(p) != "unknown")


@dataclass
class VisualAsset:
    path: Path
    kind: str  # "video" | "image"
    signatures: list[np.ndarray]  # coarse-sampled, in order; a single-item list for images
    duration_s: float


@dataclass
class AudioAsset:
    path: Path
    waveform: np.ndarray
    duration_s: float


def build_visual_bank(assets_dir: Path, tmp_dir: Path, *, fps: float = 1 / COARSE_SAMPLE_INTERVAL) -> dict[str, VisualAsset]:
    bank: dict[str, VisualAsset] = {}
    for path in asset_files(assets_dir):
        kind = guess_kind(path)
        if kind == "image":
            bank[path.name] = VisualAsset(path=path, kind=kind, signatures=[extract_image_signature(path)], duration_s=0.0)
        elif kind == "video":
            duration = probe.probe_duration_seconds(str(path))
            signatures = extract_frame_signatures(path, tmp_dir, fps=fps)
            bank[path.name] = VisualAsset(path=path, kind=kind, signatures=signatures, duration_s=duration)
    return bank


def build_audio_bank(assets_dir: Path, tmp_dir: Path, *, sample_rate: int = AUDIO_SAMPLE_RATE) -> dict[str, AudioAsset]:
    bank: dict[str, AudioAsset] = {}
    for path in asset_files(assets_dir):
        if guess_kind(path) not in ("audio", "video"):
            continue
        try:
            waveform = extract_audio_waveform(path, tmp_dir, sample_rate=sample_rate)
        except ReconstructError:
            continue  # e.g. a video asset with no audio stream
        if waveform.size == 0:
            continue
        duration = probe.probe_duration_seconds(str(path))
        bank[path.name] = AudioAsset(path=path, waveform=waveform, duration_s=duration)
    return bank


# -- matching ------------------------------------------------------------------


@dataclass
class VideoMatch:
    asset_name: str
    mean_distance: float


def identify_visual_asset(segment_signatures: list[np.ndarray], bank: dict[str, VisualAsset]) -> Optional[VideoMatch]:
    """Nearest-neighbor signature distance against every sampled frame of
    every candidate asset, majority vote across the segment's own sampled
    frames. Returns None if the bank is empty or the segment has no frames."""
    if not segment_signatures or not bank:
        return None
    votes: dict[str, int] = {name: 0 for name in bank}
    total_distance: dict[str, float] = {name: 0.0 for name in bank}
    for frame_sig in segment_signatures:
        best_name, best_dist = None, float("inf")
        for name, asset in bank.items():
            for cand in asset.signatures:
                d = float(np.linalg.norm(frame_sig - cand))
                if d < best_dist:
                    best_name, best_dist = name, d
        if best_name is not None:
            votes[best_name] += 1
            total_distance[best_name] += best_dist
    winner = max(votes, key=lambda n: votes[n])
    if votes[winner] == 0:
        return None
    return VideoMatch(asset_name=winner, mean_distance=total_distance[winner] / votes[winner])


def find_trim_offset(segment_signatures_fine: list[np.ndarray], asset_signatures_fine: list[np.ndarray]) -> Optional[tuple[int, float]]:
    """Slides the segment's (fine-grained, native-fps) signatures across the
    matched asset's own fine-grained signature sequence, returning the
    (start_frame_index, mean_distance) of the best-matching window. This is
    the same sliding-window search used to recover exact in/out points,
    validated once an unrelated kdgen bug (nonzero `in:` being doubled when
    generating XML — see xmlgen.py, mltframework/mlt#1282) stopped
    corrupting the ground truth used to test it."""
    n_seg, n_asset = len(segment_signatures_fine), len(asset_signatures_fine)
    if n_seg == 0 or n_asset < n_seg:
        return None
    seg = np.stack(segment_signatures_fine)
    best_start, best_dist = -1, float("inf")
    for start in range(n_asset - n_seg + 1):
        window = np.stack(asset_signatures_fine[start:start + n_seg])
        dist = float(np.mean(np.linalg.norm(seg - window, axis=1)))
        if dist < best_dist:
            best_start, best_dist = start, dist
    return best_start, best_dist


def _valid_correlate(reference: np.ndarray, query: np.ndarray) -> np.ndarray:
    """`len(reference) - len(query) + 1` dot products of `query` slid across
    `reference` — the same result as `scipy.signal.correlate(reference,
    query, mode="valid")`, via FFT convolution instead of a scipy
    dependency (verified numerically against scipy while developing this)."""
    n, m = len(reference), len(query)
    if n < m:
        return np.array([])
    fft_size = 1
    while fft_size < n + m - 1:
        fft_size *= 2
    full = np.fft.irfft(np.fft.rfft(reference, fft_size) * np.fft.rfft(query[::-1], fft_size), fft_size)
    start = m - 1
    return full[start:start + (n - m + 1)]


@dataclass
class AudioMatch:
    asset_name: str
    offset_s: float
    score: float  # normalized correlation peak, ~1.0 for a clean match, ~0.0 for unrelated audio
    asset_is_longer: bool  # see identify_audio_asset


def identify_audio_asset(segment_waveform: np.ndarray, bank: dict[str, AudioAsset], *, sample_rate: int = AUDIO_SAMPLE_RATE) -> Optional[AudioMatch]:
    """`_valid_correlate` needs the reference to be at least as long as the
    query, so whichever of (segment, asset) is longer is used as the
    reference - a segment isn't necessarily shorter than the asset it came
    from: e.g. reconstruct() matches a whole render's audio track against
    each candidate, and the render is commonly longer than any single
    audio clip used partway through it (trailing/leading silence once the
    audio has ended but the video hasn't).

    `AudioMatch.offset_s` means different things depending on which side
    was longer (`asset_is_longer`): if the asset is the (>=) reference,
    it's the trim-in point *within the asset* where the segment's content
    starts; if the segment is the reference, it's where *within the
    segment* the asset's content starts."""
    if segment_waveform.size == 0 or not bank:
        return None
    segment_norm = segment_waveform / (np.linalg.norm(segment_waveform) + 1e-9)
    best: Optional[AudioMatch] = None
    for name, asset in bank.items():
        asset_norm = asset.waveform / (np.linalg.norm(asset.waveform) + 1e-9)
        asset_is_longer = asset.waveform.size >= segment_waveform.size
        reference, query = (asset_norm, segment_norm) if asset_is_longer else (segment_norm, asset_norm)
        corr = _valid_correlate(reference, query)
        if corr.size == 0:
            continue
        peak_idx = int(np.argmax(corr))
        score = float(corr[peak_idx])
        if best is None or score > best.score:
            best = AudioMatch(asset_name=name, offset_s=peak_idx / sample_rate, score=score, asset_is_longer=asset_is_longer)
    return best


# -- scene-cut segmentation ----------------------------------------------------


@dataclass
class Segment:
    start_s: float
    end_s: float


_MAD_TO_STDEV = 1.4826  # scales MAD to be comparable to a standard deviation for normal data


def segment_by_scene_cuts(
    coarse_signatures: list[np.ndarray],
    *,
    interval: float = COARSE_SAMPLE_INTERVAL,
    sensitivity: float = 6.0,
    window: int = 3,
    global_floor_fraction: float = 0.5,
) -> list[Segment]:
    """Splits into segments wherever consecutive-frame signature distance is
    a *local* statistical outlier, instead of a fixed absolute threshold: a
    "big jump" looks very different in absolute terms between an
    already-busy render and a mostly-static one, so the cutoff has to
    adapt to the render's own content rather than being a constant. Reuses
    the same coarse signatures matching already samples with, instead of a
    second scene-detection method/dependency.

    Each diff is compared against median + `sensitivity` * MAD (median
    absolute deviation) of its own `window`-sized neighborhood, not the
    whole sequence's median/MAD. A purely *global* baseline breaks down as
    soon as a render mixes footage with different natural noise levels
    (e.g. a near-static synthetic test pattern next to real, grainy
    footage): the low-noise portion drags the global median/MAD down so
    far that the noisier portion's perfectly normal frame-to-frame
    variance reads as an outlier too, alongside the real cut.

    But a purely *local* baseline has the opposite failure: in a stretch
    that happens to be unusually uniform, the local MAD can collapse
    towards zero, making even a small, perfectly ordinary fluctuation look
    like a huge relative jump. So the MAD actually used is
    `max(local_mad, global_mad * global_floor_fraction)` - local enough to
    judge each region against its own baseline noise, but never allowed to
    collapse below a fraction of the render's overall noise floor. Both
    failure modes were reproduced directly against real test data before
    landing on this (see tests/fixtures/public_domain/ and
    tests/test_reconstruct.py) - a pure global MAD flagged two ordinary
    diffs (~300) as cuts because a quieter clip elsewhere in the same
    render anchored the global baseline too low; a pure local MAD flagged
    an ordinary diff (132) as a cut because its immediate neighbors
    happened to be unusually tight (~112-115)."""
    n = len(coarse_signatures)
    if n <= 1:
        return [Segment(0.0, n * interval)]

    diffs = np.array([
        float(np.linalg.norm(coarse_signatures[i] - coarse_signatures[i - 1]))
        for i in range(1, n)
    ])
    m = len(diffs)
    global_median = np.median(diffs)
    global_mad = np.median(np.abs(diffs - global_median))
    cut_indices = []
    for i in range(m):
        lo, hi = max(0, i - window), min(m, i + window + 1)
        neighborhood = np.concatenate([diffs[lo:i], diffs[i + 1:hi]])
        if neighborhood.size == 0:
            continue
        local_median = np.median(neighborhood)
        local_mad = np.median(np.abs(neighborhood - local_median))
        effective_mad = max(local_mad, global_mad * global_floor_fraction)
        cutoff = local_median + sensitivity * _MAD_TO_STDEV * effective_mad
        if diffs[i] > cutoff:
            cut_indices.append(i + 1)

    boundaries = [0, *cut_indices, n]
    return [
        Segment(start_idx * interval, end_idx * interval)
        for start_idx, end_idx in zip(boundaries, boundaries[1:])
    ]


# -- fade detection -------------------------------------------------------------


def _detect_ramp_duration(levels: list[float], interval: float, *, near_zero_fraction: float = 0.08, tolerance_fraction: float = 0.08) -> float:
    """Shared by video (brightness) and audio (RMS) fade detection - both
    boil down to "does a level signal ramp up from near-zero to the
    surrounding plateau." `levels` must already start at the point being
    tested: segment start for a fade_in, or the segment's last samples
    *reversed* for a fade_out. Returns the ramp duration, or 0.0 if the
    signal doesn't start near-zero (no fade).

    Both thresholds are fractions *of the plateau level*, not absolute -
    audio's `levels` are pre-normalized to its own peak (plateau ~= 1.0) so
    this doesn't change anything there, but video brightness isn't
    normalized (a solid red frame has plateau ~0.33, not 1.0, since only
    one of three RGB channels is lit), and a fixed absolute tolerance
    reads as a much bigger fraction of a dim clip's range than a bright
    one's - measured to systematically underestimate fade duration by
    ~20% on a red test clip before this was made relative."""
    if not levels:
        return 0.0
    plateau = max(levels)
    near_zero = near_zero_fraction * plateau
    tolerance = tolerance_fraction * plateau
    if levels[0] > near_zero:
        return 0.0
    if plateau <= near_zero:
        return 0.0  # the whole window is near-silent/near-black, not a fade
    for i, level in enumerate(levels):
        if level >= plateau - tolerance:
            return i * interval
    return len(levels) * interval


def _video_brightness(signature: np.ndarray) -> float:
    return float(np.mean(signature)) / 255.0


def detect_video_fade(fine_signatures: list[np.ndarray], interval: float, *, at_end: bool = False) -> float:
    """`fine_signatures` must be sampled at a fixed `interval` (seconds)
    starting at the segment's own start (for fade_in) or covering its last
    stretch (for fade_out, pass `at_end=True` - the samples are reversed
    internally, so pass them in their normal forward order either way)."""
    levels = [_video_brightness(s) for s in fine_signatures]
    if at_end:
        levels = levels[::-1]
    return _detect_ramp_duration(levels, interval)


def _audio_rms_windows(waveform: np.ndarray, sample_rate: int, window_s: float) -> list[float]:
    window = max(1, int(window_s * sample_rate))
    n_windows = len(waveform) // window
    return [
        float(np.sqrt(np.mean(waveform[i * window:(i + 1) * window] ** 2)))
        for i in range(n_windows)
    ]


def detect_audio_fade(waveform: np.ndarray, sample_rate: int, *, window_s: float = 0.15, at_end: bool = False) -> float:
    """`waveform` covers the segment's own audio, starting at its own start
    (for fade_in) or full segment audio (for fade_out, pass `at_end=True` -
    same forward-order convention as `detect_video_fade`)."""
    levels = _audio_rms_windows(waveform, sample_rate, window_s)
    if not levels:
        return 0.0
    peak = max(levels) or 1.0
    normalized = [level / peak for level in levels]
    if at_end:
        normalized = normalized[::-1]
    return _detect_ramp_duration(normalized, window_s)


def _active_audio_duration(waveform: np.ndarray, sample_rate: int, *, window_s: float = 0.1, threshold_fraction: float = 0.02) -> float:
    """How much of `waveform`, from the start, actually has audio content
    (RMS above a small fraction of the loudest window) - trims trailing
    silence. Needed because a render's video track commonly runs longer
    than its audio track (e.g. audio ends at 3s, but a following video-only
    clip keeps the render going to 3.5s): without this, a matched asset
    that's at least as long as the *whole* render gets credited with that
    whole duration, well past where its actual content stopped."""
    levels = _audio_rms_windows(waveform, sample_rate, window_s)
    if not levels:
        return 0.0
    threshold = max(levels) * threshold_fraction
    last_active = 0
    for i, level in enumerate(levels):
        if level > threshold:
            last_active = i
    return (last_active + 1) * window_s


# -- YAML assembly --------------------------------------------------------------


@dataclass
class ReconstructResult:
    data: dict
    warnings: list[str] = field(default_factory=list)


def _make_asset_ids(names: set[str]) -> dict[str, str]:
    """Maps asset filenames (bank keys) to short kdgen bin ids: the
    filename stem, disambiguated the same way reverse.py's
    register_bin_clip is if two different assets happen to share one."""
    ids: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(names):
        base = Path(name).stem or "clip"
        candidate, n = base, 2
        while candidate in used:
            candidate = f"{base}_{n}"
            n += 1
        used.add(candidate)
        ids[name] = candidate
    return ids


def _quantized_duration(seconds: float, fps: float) -> float:
    """Rounds `seconds` the same way schema.py will when it resolves the
    generated YAML (seconds -> whole frames -> seconds), so a duration
    computed here doesn't drift from what `kdgen validate` independently
    re-derives from the same source file - ffprobe's raw duration and its
    frame-quantized equivalent can differ by a frame or more (e.g. a
    3.056s source becomes 3.04s at 25fps), and clamping against the wrong
    one still lets a slightly-too-long `in + duration` through."""
    frame_rate = resolve_fps(fps)
    return frame_rate.frames_to_seconds(frame_rate.seconds_to_frames(seconds))


def reconstruct(video_path: Path, assets_dir: Path) -> ReconstructResult:
    """Top-level entry point: given an already-rendered video and a folder
    of candidate source assets, produces a kdgen-compatible YAML dict by
    content-based matching (see the module docstring - there's no
    guaranteed correct answer here). Unmatched or low-confidence segments
    are never silently guessed: they're flagged under
    `x_reconstruct_unmatched` instead, the same way `reverse.py` doesn't
    discard what it doesn't recognize under `x_kdenlive_raw`.

    Audio is treated as a single clip spanning the whole render, not
    segmented to match the video cuts: background audio commonly doesn't
    align with visual cuts at all, and segmenting audio by *its own*
    content changes is a real, much harder, separate problem this doesn't
    attempt."""
    if not (probe.ffmpeg_available() and probe.ffprobe_available()):
        raise ReconstructError(msg("reconstruct.ffmpeg_or_ffprobe_missing"))

    video_path = video_path.resolve()
    assets_dir = assets_dir.resolve()
    warnings: list[str] = []
    unmatched: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="kdgen-reconstruct-") as tmp:
        tmp_dir = Path(tmp)
        info = probe_video_info(video_path)
        fps, width, height = info["fps"], info["width"], info["height"]

        visual_bank = build_visual_bank(assets_dir, tmp_dir)
        audio_bank = build_audio_bank(assets_dir, tmp_dir)
        asset_ids = _make_asset_ids(set(visual_bank) | set(audio_bank))

        coarse_signatures = extract_frame_signatures(video_path, tmp_dir, fps=1 / COARSE_SAMPLE_INTERVAL)
        segments = segment_by_scene_cuts(coarse_signatures)

        video_clips: list[dict] = []
        fine_asset_cache: dict[str, list[np.ndarray]] = {}

        for segment in segments:
            start_idx = round(segment.start_s / COARSE_SAMPLE_INTERVAL)
            end_idx = round(segment.end_s / COARSE_SAMPLE_INTERVAL)
            duration_s = segment.end_s - segment.start_s
            match = identify_visual_asset(coarse_signatures[start_idx:end_idx], visual_bank)

            if match is None or match.mean_distance > VIDEO_MATCH_DISTANCE_THRESHOLD:
                where = f"video [{segment.start_s:.2f}s-{segment.end_s:.2f}s]"
                unmatched.append({
                    "where": where,
                    "best_guess": match.asset_name if match else None,
                    "confidence": None if match is None else round(match.mean_distance, 1),
                })
                warnings.append(msg("reconstruct.no_confident_match", where=where))
                continue

            asset = visual_bank[match.asset_name]
            clip: dict = {"clip": asset_ids[match.asset_name], "start": round(segment.start_s, 3)}
            fade_in = fade_out = 0.0

            if asset.kind == "video":
                segment_fine = extract_frame_signatures(
                    video_path, tmp_dir, fps=fps, start_s=segment.start_s, duration_s=duration_s
                )
                if match.asset_name not in fine_asset_cache:
                    fine_asset_cache[match.asset_name] = extract_frame_signatures(asset.path, tmp_dir, fps=fps)
                trim = find_trim_offset(segment_fine, fine_asset_cache[match.asset_name])
                in_s = (trim[0] / fps) if trim else 0.0
                if in_s > 1e-6:
                    clip["in"] = round(in_s, 3)
                # Trim recovery has a real, expected +/- 1-2 frame margin of
                # error (compression noise, sampling granularity - see
                # find_trim_offset); clamp so a slightly-over in+duration
                # from that noise never claims more of the source than it
                # actually has, which schema.py would otherwise reject.
                duration_s = min(duration_s, _quantized_duration(asset.duration_s, fps) - in_s)
                interval = 1 / fps
                fade_in = detect_video_fade(segment_fine, interval)
                fade_out = detect_video_fade(segment_fine, interval, at_end=True)

            clip["duration"] = round(duration_s, 3)
            if fade_in > 0:
                clip["fade_in"] = round(fade_in, 3)
            if fade_out > 0:
                clip["fade_out"] = round(fade_out, 3)
            video_clips.append(clip)

        audio_clips: list[dict] = []
        full_waveform = extract_audio_waveform(video_path, tmp_dir)
        if full_waveform.size > 0 and audio_bank:
            audio_match = identify_audio_asset(full_waveform, audio_bank)
            if audio_match is None or audio_match.score < AUDIO_MATCH_SCORE_THRESHOLD:
                where = "audio [full track]"
                unmatched.append({
                    "where": where,
                    "best_guess": audio_match.asset_name if audio_match else None,
                    "confidence": None if audio_match is None else round(audio_match.score, 3),
                })
                warnings.append(msg("reconstruct.no_confident_match", where=where))
            else:
                clip = {"clip": asset_ids[audio_match.asset_name], "start": 0.0}
                if audio_match.asset_is_longer:
                    # The asset covers (at least) the whole render: offset_s
                    # is the trim-in point within the asset.
                    if audio_match.offset_s > 1e-6:
                        clip["in"] = round(audio_match.offset_s, 3)
                    asset_duration_s = _quantized_duration(audio_bank[audio_match.asset_name].duration_s, fps)
                    # Not the render's full duration: the render's video
                    # track commonly runs longer than its audio (silent
                    # tail), so use where the audio content actually stops.
                    active_duration_s = _active_audio_duration(full_waveform, AUDIO_SAMPLE_RATE)
                    # Same clamp as the video branch: correlation-based
                    # offset recovery has a small margin of error, don't
                    # let it claim more of the source than it actually has.
                    clip["duration"] = round(min(active_duration_s, asset_duration_s - audio_match.offset_s), 3)
                    clip_waveform = full_waveform[:round(active_duration_s * AUDIO_SAMPLE_RATE)]
                else:
                    # The render's full track is longer than the asset (e.g.
                    # trailing/leading silence once the audio ends but the
                    # video doesn't): offset_s is where in the render the
                    # asset's own content starts.
                    clip["start"] = round(audio_match.offset_s, 3)
                    asset_duration_s = _quantized_duration(audio_bank[audio_match.asset_name].duration_s, fps)
                    clip["duration"] = round(asset_duration_s, 3)
                    start_sample = round(audio_match.offset_s * AUDIO_SAMPLE_RATE)
                    end_sample = start_sample + round(asset_duration_s * AUDIO_SAMPLE_RATE)
                    clip_waveform = full_waveform[start_sample:end_sample]

                fade_in = detect_audio_fade(clip_waveform, AUDIO_SAMPLE_RATE)
                fade_out = detect_audio_fade(clip_waveform, AUDIO_SAMPLE_RATE, at_end=True)
                if fade_in > 0:
                    clip["fade_in"] = round(fade_in, 3)
                if fade_out > 0:
                    clip["fade_out"] = round(fade_out, 3)
                audio_clips.append(clip)

        used_ids = {c["clip"] for c in video_clips} | {c["clip"] for c in audio_clips}
        name_by_id = {asset_id: name for name, asset_id in asset_ids.items()}
        bin_entries = [
            {
                "id": used_id,
                "path": str((visual_bank.get(name_by_id[used_id]) or audio_bank[name_by_id[used_id]]).path),
            }
            for used_id in sorted(used_ids)
        ]

        tracks = []
        if video_clips:
            tracks.append({"type": "video", "name": "V1", "clips": video_clips})
        if audio_clips:
            tracks.append({"type": "audio", "name": "A1", "clips": audio_clips})

        data: dict = {
            "project": {
                "id": video_path.stem,
                "title": video_path.stem,
                "fps": int(fps) if fps == int(fps) else round(fps, 3),
                "width": width,
                "height": height,
            },
            "bin": bin_entries,
            "timeline": {"tracks": tracks},
        }
        if unmatched:
            data["x_reconstruct_unmatched"] = unmatched

        return ReconstructResult(data=data, warnings=warnings)
