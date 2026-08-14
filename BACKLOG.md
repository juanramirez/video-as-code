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
  - **Not yet tested:** real footage with actual motion/texture (only
    tried ffmpeg `testsrc` synthetic patterns and solid colors, both
    easier than handheld video); precise trim-point recovery when only a
    sub-range of a longer source asset appears in the render (only tested
    whole-asset offset matching); scene-cut segmentation on a render with
    more than two back-to-back clips.
  - **Bottom line:** matching is feasible and the audio side looks solid;
    the video side needs a real (non-degenerate) frame signature, not
    off-the-shelf aHash, before going further. Worth prototyping trim-point
    precision and real footage next, still before committing to an
    architecture.
