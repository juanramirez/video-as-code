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
