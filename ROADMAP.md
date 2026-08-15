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
- **`kdgen reconstruct`**: reconstruct a YAML from an already-rendered
  video + an assets folder, via content-based matching (not XML parsing —
  there's no structure to walk, just pixels and audio samples). A separate
  tool/subcommand, not an extension of `reverse.py` — doesn't share
  `Importer`'s XML-tree-walking logic, needs its own tolerance-for-error
  model (this will never be as exact as the `.kdenlive` import), its own
  tests.

  Prototype findings so far (2026-08-14, throwaway scripts, not in the
  repo):
  - *Video matching*: naive average-hash (aHash: resize 8x8, threshold
    against the frame's own mean) mostly worked, but has a sharp failure
    mode — any flat/solid-color frame hashes to the same all-zero vector
    regardless of hue, since every pixel equals the mean. An unrelated
    distractor frame matched a real asset perfectly (distance 0) because
    of this. Fix: compare raw downsampled RGB values directly (Euclidean
    distance), not a binarized hash.
  - *Audio matching*: cross-correlation of downsampled (8kHz) waveforms is
    strong and clean — real match scored 0.97 (normalized peak), an
    unrelated distractor scored ~0.00.
  - *Trim-point recovery*: initially looked broken on textured/moving
    content, but wasn't a matching problem — it surfaced a real kdgen bug
    (nonzero `in:` was double-applied when generating XML; see the v0.1
    fix, `xmlgen.py`/`_build_track`, and
    https://github.com/mltframework/mlt/issues/1282). Now unblocked.
  - **Not yet tested**: real (non-synthetic) footage with actual
    motion/texture; scene-cut segmentation on a render with more than two
    back-to-back clips.
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
