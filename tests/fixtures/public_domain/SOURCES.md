# Sources

Real (non-synthetic) media for testing `reconstruct.py` against actual
photographic/video content, not just flat colors or generated test
patterns (see `reconstruct.py`'s module docstring and `ROADMAP.md` for
why this matters — matching was only validated against synthetic content
before these were added). Both are public domain; no attribution is
required, but it's recorded here for provenance.

## `cat.jpg`

- Source: [Black barn cat - Public Domain (2014 photo; cropped
  2022)](https://commons.wikimedia.org/wiki/File:Black_barn_cat_-_Public_Domain_(2014_photo;_cropped_2022).jpg),
  Wikimedia Commons.
- License: CC0 (public domain dedication).
- Modified: downscaled to fit 320x320 and re-encoded as JPEG (quality 75)
  to keep the file tiny, consistent with `examples/simple/assets/`.
  Retrieved and processed 2026-08-15.

## `calcutta.mp4`

- Source: [Panorama of
  Calcutta](https://commons.wikimedia.org/wiki/File:Panorama_of_Calcutta.webm),
  Wikimedia Commons (original film, public domain).
- License: Public domain.
- Modified: 3-second clip trimmed from the original ~98s film (starting
  at 0:20), downscaled to 320px wide, silent, re-encoded as H.264 to keep
  the file tiny. Retrieved and processed 2026-08-15.
