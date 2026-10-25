"""Conversion between seconds and frames, and resolution of fps fractions.

MLT (and hence kdenlive) accepts raw frame numbers as the value of the
``in``/``out`` attributes and as keyframe keys, instead of ``HH:MM:SS.mmm``
timecodes. We always use whole frames: it avoids timecode rounding ambiguity
and makes the output deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

# Common non-integer fps (NTSC) -> exact num/den fraction as used by MLT.
_NTSC_FPS = {
    23.976: Fraction(24000, 1001),
    29.97: Fraction(30000, 1001),
    47.952: Fraction(48000, 1001),
    59.94: Fraction(60000, 1001),
}


@dataclass(frozen=True)
class FrameRate:
    num: int
    den: int

    @property
    def fps(self) -> float:
        return self.num / self.den

    def seconds_to_frames(self, seconds: float) -> int:
        """Rounds to the nearest frame; exact ties (e.g. 0.5s at 25fps = 12.5
        frames) always round up (round-half-up), unlike Python's native
        round-half-to-even, which is unpredictable here: it would depend on
        the frame number's parity rather than actual closeness."""
        return math.floor(seconds * self.num / self.den + 0.5)

    def frames_to_seconds(self, frames: int) -> float:
        return frames * self.den / self.num


def resolve_fps(fps: float | int | str) -> FrameRate:
    """Accepts an integer (25, 30...) or a common NTSC float (23.976, 29.97...)."""
    if isinstance(fps, str):
        fps = float(fps)
    if isinstance(fps, int) or float(fps).is_integer():
        return FrameRate(int(fps), 1)
    for value, frac in _NTSC_FPS.items():
        if abs(fps - value) < 0.005:
            return FrameRate(frac.numerator, frac.denominator)
    # Fallback: approximate with denominator 1001 if it looks like NTSC, else as a simple fraction.
    frac = Fraction(fps).limit_denominator(100000)
    return FrameRate(frac.numerator, frac.denominator)


def parse_clock(text: str) -> float:
    """Seconds from `80`, `80.5`, `1:20`, `1:20.5` or `1:02:03`. ValueError
    if it's none of them."""
    fields = text.strip().split(":")
    if not 1 <= len(fields) <= 3 or any(not f for f in fields):
        raise ValueError(text)
    *whole, last = fields
    try:
        seconds = float(last)
    except ValueError:
        raise ValueError(text) from None
    if not math.isfinite(seconds) or seconds < 0 or (whole and seconds >= 60):
        raise ValueError(text)
    total = 0
    for i, field in enumerate(whole):
        if not field.isdigit() or (i > 0 and int(field) >= 60):
            raise ValueError(text)
        total = total * 60 + int(field)
    return total * 60 + seconds


def clock_name(seconds: float) -> str:
    """For a file name: `1m20s`, `1m20.5s`, `45s`."""
    minutes, rest = divmod(round(seconds, 2), 60)
    rest_text = f"{rest:g}s"
    return f"{int(minutes)}m{rest_text}" if minutes else rest_text
