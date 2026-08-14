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
"""
from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from . import probe

SIGNATURE_SIZE = 8  # frame signatures are SIGNATURE_SIZE x SIGNATURE_SIZE x 3 floats
COARSE_SAMPLE_INTERVAL = 0.2  # seconds, for identifying which asset a segment came from
AUDIO_SAMPLE_RATE = 8000  # Hz, downsampled for cross-correlation

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
        raise ReconstructError(f"ffmpeg failed: {result.stderr.strip()}")


def _signature_from_image(img: Image.Image) -> np.ndarray:
    small = img.convert("RGB").resize((SIGNATURE_SIZE, SIGNATURE_SIZE), Image.LANCZOS)
    return np.asarray(small, dtype=np.float64).flatten()


def extract_frame_signatures(path: Path, tmp_dir: Path, *, fps: float) -> list[np.ndarray]:
    """Bulk-extracts frames at `fps` and returns one signature per frame, in
    order. Bulk extraction (one ffmpeg call, `-vf fps=`) instead of one call
    per timestamp — much faster for anything beyond a handful of frames."""
    out_dir = tmp_dir / f"frames_{path.stem}_{fps}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(["-i", str(path), "-vf", f"fps={fps}", str(out_dir / "f_%06d.png")])
    signatures = []
    for png in sorted(out_dir.glob("f_*.png")):
        with Image.open(png) as img:
            signatures.append(_signature_from_image(img))
    return signatures


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
        raise ReconstructError(f"unexpected WAV sample width from ffmpeg: {sample_width}")
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


def identify_audio_asset(segment_waveform: np.ndarray, bank: dict[str, AudioAsset], *, sample_rate: int = AUDIO_SAMPLE_RATE) -> Optional[AudioMatch]:
    if segment_waveform.size == 0 or not bank:
        return None
    query = segment_waveform / (np.linalg.norm(segment_waveform) + 1e-9)
    best: Optional[AudioMatch] = None
    for name, asset in bank.items():
        if asset.waveform.size < query.size:
            continue
        reference = asset.waveform / (np.linalg.norm(asset.waveform) + 1e-9)
        corr = _valid_correlate(reference, query)
        if corr.size == 0:
            continue
        peak_idx = int(np.argmax(corr))
        score = float(corr[peak_idx])
        if best is None or score > best.score:
            best = AudioMatch(asset_name=name, offset_s=peak_idx / sample_rate, score=score)
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
    sensitivity: float = 3.5,
) -> list[Segment]:
    """Splits into segments wherever consecutive-frame signature distance is
    a statistical outlier, instead of a fixed absolute threshold: a "big
    jump" looks very different in absolute terms between an already-busy
    render and a mostly-static one, so the cutoff has to adapt to the
    render's own content rather than being a constant. Reuses the same
    coarse signatures matching already samples with, instead of a second
    scene-detection method/dependency.

    Uses median + `sensitivity` * MAD (median absolute deviation), not
    mean + k*stdev: a real cut is often 1-2 frames out of dozens, and mean/
    stdev get dragged so far up by those few extreme values that the
    threshold ends up close to the outliers themselves (verified against a
    real 3-clip render: cuts measured ~2200, normal frame-to-frame diffs
    ~0-130, but mean+3*stdev came out to ~2240 - just above *both* real
    cuts). Median/MAD barely move for a couple of outliers in a larger
    sample, so the threshold stays anchored to the "normal" diffs instead."""
    n = len(coarse_signatures)
    if n <= 1:
        return [Segment(0.0, n * interval)]

    diffs = np.array([
        float(np.linalg.norm(coarse_signatures[i] - coarse_signatures[i - 1]))
        for i in range(1, n)
    ])
    median = np.median(diffs)
    mad = np.median(np.abs(diffs - median))
    cutoff = median + sensitivity * _MAD_TO_STDEV * mad
    cut_indices = [i + 1 for i, d in enumerate(diffs) if d > cutoff]

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
