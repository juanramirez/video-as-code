# Backlog

Unordered pool of things we want to do at some point — no priority, no
version assigned. When one of these gets scheduled into an actual release,
it moves to `ROADMAP.md` under that version; it stays here until then.

- **Reconstruct a YAML from a rendered video + an assets folder.** Given a
  finished/exported video file (not a `.kdenlive` project — an actual
  `.mp4` or similar) and a folder of the source assets that presumably
  went into it, produce a `kdgen`-compatible YAML that recreates it: which
  asset appears where on the timeline, trimmed how, with what fades.

  This is a different problem from `reverse.py`, not an extension of it:
  `kdgen import` today parses a `.kdenlive` file, a structured XML document
  that already says explicitly "this producer, this in/out, this filter" —
  a deterministic, lossless-where-recognized translation between
  structured formats. A rendered video has no structure to parse, just
  pixels and audio samples. Recovering "which source clip, trimmed how"
  needs *content-based matching* instead — comparing the rendered video
  against the candidate assets — which is a different technique stack:
  scene-cut detection to segment the render, perceptual/frame hashing or
  audio fingerprinting to match segments against the asset pool (exact
  comparison won't work — the render is re-encoded/resized/recompressed),
  cross-correlation to find precise trim points, and fade detection from
  pixel brightness/audio volume ramps instead of reading an explicit
  filter. Unlike XML parsing there's no guaranteed correct answer —
  ambiguous matches, near-duplicate assets, or clips that were never in
  the folder at all are all possible.

  When this gets picked up: don't bolt it onto `reverse.py`, it doesn't
  share `Importer`'s XML-tree-walking logic at all. Closer to a separate
  tool/subcommand (`kdgen reconstruct`?) built on a fingerprinting/matching
  library, with its own tests and its own tolerance-for-error model. Flagged
  2026-08-14 — the original ask for a "reverse converter" covered both this
  and the already-implemented `.kdenlive` -> YAML import; only the latter is
  done.

  **Matching prototype (2026-08-14, throwaway script, not in the repo):**
  built a rendered "export" from `examples/simple/project.yaml` via
  `melt -consumer avformat:`, then tried to match it back to
  `examples/simple/assets/` using only the rendered file + the assets
  folder (no YAML/metadata).
  - *Video*, naive average-hash (aHash: resize 8x8, threshold against the
    frame's own mean) on sampled frames: correctly identified the
    photo/clip boundary within one sample step, and stayed correct even
    after re-encoding the render at 96x54 / crf 40 (heavy
    compression+downscale). **But it has a real, sharp failure mode:** for
    any flat/solid-color frame, every pixel equals the mean, so
    `pixel > mean` is `False` everywhere — *every* solid color hashes to
    the identical all-zero vector, regardless of hue. A green distractor
    frame that never appeared in the render "matched" the blue photo
    perfectly (distance 0) because of this, not because it looked similar.
    Fix that worked: compare the raw resized-color values directly
    (Euclidean distance on downsampled RGB) instead of a binarized hash —
    separated a near-duplicate blue (dist 72) from the real photo cleanly,
    and both from an unrelated green (dist ~1400+). Point: don't use
    textbook aHash as-is; keep the absolute color information.
  - *Audio*, cross-correlation of downsampled (8kHz) waveforms: strong and
    clean — real match scored 0.97 (normalized peak), an unrelated
    distractor tone scored ~0.00. A "not present" threshold looks easy to
    set here.
  - **Not yet tested:** scene-cut segmentation on a render with more than
    two back-to-back clips.
  - **Bottom line:** matching is feasible and the audio side looks solid;
    the video side needs a real (non-degenerate) frame signature, not
    off-the-shelf aHash. Trim-point recovery and real (textured/moving)
    footage were tested next (see below) and turned up something bigger
    than expected.

  **Trim-point + real-footage follow-up (2026-08-14):** tried recovering
  the precise in-point of a render that only used a *middle slice* of a
  longer source clip, using textured/moving synthetic footage (an evolving
  mandelbrot zoom, not flat colors or a static test pattern) instead of the
  first prototype's easy content. The recovered offset was confidently
  wrong — not noisy/ambiguous, just wrong, by a suspiciously clean amount.
  Chased it down and it wasn't the matching approach at all: **it's an
  upstream MLT bug.** An XML-defined producer/chain with a non-zero `in`
  attribute, read for more than one frame, starts reading from `2×in`
  instead of `in`. Confirmed independent of kdgen, of this project's
  sandboxed melt invocation (reproduces on a clean `apt`-installed melt
  too), of GOP/keyframe structure, and of the qtblend/Qt6-missing
  limitation documented elsewhere in this repo. Filed upstream:
  https://github.com/mltframework/mlt/issues/1282.

  This also means **our melt-validation tests
  (`tests/test_melt_validation.py`) wouldn't have caught this** — they
  check duration, not per-frame pixel content, and this bug doesn't change
  duration, only which frames show up. Worth a follow-up test once/if the
  upstream bug is fixed (or a workaround is found) — probably not
  worthwhile before that, since a real fix path doesn't exist yet.
  Trim-point recovery for the reconstruct-from-video idea itself is
  blocked on the same fix (or a workaround, e.g. avoid relying on a single
  producer's `in`/`out` for a sub-range read).
