# Backlog

Unordered pool of things we want to do at some point — no priority, no
version assigned. When one of these gets scheduled into an actual release,
it moves to `ROADMAP.md` under that version; it stays here until then.

- Only `en`/`es` exist in the i18n catalog (`i18n.py`). Adding a locale is
  just adding another dict and an entry in `CATALOGS` — no architecture
  change needed, just translation work.

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
  library, with its own tests and its own tolerance-for-error model. Worth
  prototyping the matching step in isolation before committing to an
  architecture. Flagged 2026-08-14 — the original ask for a "reverse
  converter" covered both this and the already-implemented `.kdenlive` ->
  YAML import; only the latter is done.
