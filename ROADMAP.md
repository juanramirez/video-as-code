# Roadmap

What ships in which version. Unlike `BACKLOG.md` (an unordered pool of
things we might want), this file is about sequencing: what's already out,
and what's committed to for the next release. An item only belongs here
once it's actually been assigned to a version — until then it stays in the
backlog.

## v0.1 — shipped (2026-08-14)

- YAML schema and validation, with a high-level `scenes:` compiler on top
  of the low-level track/clip model.
- Deterministic `.kdenlive` (MLT XML) generation.
- Reverse importer, `.kdenlive` -> YAML, preserving unrecognized elements
  under `x_kdenlive_raw` instead of discarding them.
- CLI: `kdgen build` / `validate` / `import`.
- i18n message catalog for user-facing output (`en`/`es`, via `KDGEN_LANG`).
- Example project with tiny committed assets (`examples/simple/`).

## v0.2 — scoped (2026-08-14)

- **Mute** — **implemented** (2026-08-15): `mute: true` on a clip placement
  (video or audio) silences its audio track. Same `mlt_service="volume"`
  filter as kdenlive's own "Mute" effect
  (`share/kdenlive/effects/mute.xml`), fixed `gain=0`, applied on top of
  any fade filters. For a video-track clip this only touches audio
  samples, leaving the picture untouched — verified against real `melt`
  renders, not just plausible-looking XML
  (`tests/test_melt_validation.py::test_melt_mutes_a_video_clips_own_audio`
  measures both silence and that the picture still renders). `reverse.py`
  recognizes it on import too (`volume`/`mute` -> `mute: true`), so it
  round-trips instead of landing in `x_kdenlive_raw`.

  This is also what unblocks independent video/audio fades for one video
  file: add the same video path as two `bin:` entries, place one on a
  video track (`mute: true`, no audio) and the other on an audio track
  (its own `fade_in`/`fade_out`) — before mute existed, the video-track
  placement's own embedded audio would have played at the same time as
  the audio-track copy's faded audio, overlapping instead of the
  audio-track copy standing in cleanly for the video's sound.
- **`kdgen reconstruct`** — **implemented** (2026-08-14, branch
  `kdgen-reconstruct`, not yet merged): reconstructs a YAML from an
  already-rendered video + an assets folder, via content-based matching
  (not XML parsing — there's no structure to walk, just pixels and audio
  samples). Its own module (`reconstruct.py`) and CLI subcommand
  (`kdgen reconstruct <video> <assets_dir> [-o output.yaml]`), not an
  extension of `reverse.py` — doesn't share `Importer`'s XML-tree-walking
  logic. Needs the optional `reconstruct` extra (`numpy`, `Pillow`); the
  base install stays light, `cli.py` imports it lazily.

  Pipeline: frame/audio signature banks per candidate asset -> scene-cut
  segmentation of the render (frame-to-frame signature distance, flagged
  as a cut via a median+MAD statistical outlier, not mean+stdev — a real
  cut is such an extreme value among a handful of samples that mean/stdev
  get dragged toward it, nearly masking it; the outlier threshold itself
  blends a local window's MAD with a floor derived from the whole
  sequence's MAD, since a pure local window can mistake ordinary variation
  for a cut when its neighborhood happens to be unusually flat, and a pure
  global threshold can mistake one noisy real clip's internal variation
  for a cut when compared against a much quieter clip's baseline — tuned
  against `tests/fixtures/public_domain/calcutta.mp4`, not synthetic
  content alone) -> per-segment asset
  identification (nearest-neighbor signature distance for video,
  FFT-based cross-correlation for audio, no `scipy` dependency) ->
  trim-point recovery (sliding-window signature search) -> fade detection
  (brightness/RMS ramp, thresholds relative to each segment's own plateau
  level, not an absolute cutoff). Low-confidence or unmatched segments are
  never guessed — they land under `x_reconstruct_unmatched:` instead, the
  same way `reverse.py` uses `x_kdenlive_raw:`. Verified end-to-end against
  real `melt` renders, not just structurally
  (`tests/test_reconstruct.py::test_reconstruct_end_to_end_round_trip`).

  Along the way this surfaced two real, unrelated bugs, both found and
  fixed on `master`: the nonzero-`in:` double-trim
  (https://github.com/mltframework/mlt/issues/1282) and video
  fade_in/fade_out having no visible effect when rendered (`level`/`alpha`
  swapped in the `brightness` filter).

  **Known limitation:** matching compares against each asset's *plain*
  content, so a segment that's mid-fade (darkened/quieted relative to the
  source) can score too poorly to match confidently, and segmentation can
  split a fade into extra spurious segments. Fails safe (lands in
  `x_reconstruct_unmatched` rather than guessing wrong), but not yet
  addressed at the root.

  **Verified against real (non-synthetic) media** (2026-08-15,
  `tests/test_reconstruct.py::test_matches_real_photographic_and_film_content`,
  fixtures in `tests/fixtures/public_domain/`): a real CC0 photograph and a
  real public-domain archival film clip (actual people/architecture/camera
  motion, not a generated pattern) both matched correctly, including
  against a distractor. Not exhaustive — one photo and one short clip, not
  a stress test across varied real-world footage — but no longer purely
  synthetic-only.
- **Titles/text clips**: generate kdenlive title clips from YAML (not just
  recognize them on import — extends the generator side, not only
  `reverse.py`). Currently unsupported in both directions.
- **Speed change (timewarp)**: `speed:` on a clip placement. Well-bounded
  (one producer property), currently explicitly unsupported — `reverse.py`
  dumps timewarp clips straight to `x_kdenlive_raw` today.
- **Transitions**, two parts:
  - Verify (and likely fix) the existing `transition: dissolve` in
    `scenes.py` — it's marked in its own docstring as never verified
    against a real kdenlive project, the same "best-effort" category that
    turned out to be wrong for both audio fades and nonzero `in:` trims
    this cycle. Same treatment: real melt render + pixel check, not just
    plausible-looking XML.
  - Add wipe transitions using the luma maps kdenlive already ships
    (`share/kdenlive/lumas/`) — genuinely new, not just fixing dissolve.
