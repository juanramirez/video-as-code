# Backlog

Unordered pool of things we want to do at some point — no priority, no
version assigned. When one of these gets scheduled into an actual release,
it moves to `ROADMAP.md` under that version; it stays here until then.

- Validate against a real `melt` binary. `tests/conftest.py` already has a
  `melt` fixture (locates the binary inside kdenlive's snap if `melt` isn't
  on PATH), but it's only partially exercised so far
  (`tests/test_melt_validation.py`); coverage needs review.

- Audio fades are unverified. `_add_audio_fade_filters` in `xmlgen.py` is
  marked in the code itself as best-effort: it hasn't been checked against
  a real kdenlive project with audio fades (only video fades were
  available in the sample project used as reference). Revisit once there's
  a reference project with audio.

- Only `en`/`es` exist in the i18n catalog (`i18n.py`). Adding a locale is
  just adding another dict and an entry in `CATALOGS` — no architecture
  change needed, just translation work.

- No test locks down full round-trip idempotence byte-for-byte.
  `tests/test_reverse.py::test_second_round_trip_is_a_fixed_point` compares
  the full reimported YAML dict across two cycles, but there's no
  equivalent check comparing the *generated `.kdenlive` XML* itself
  (`kdenlive1 == kdenlive2`, not just the YAML each produces) — worth
  adding if `xmlgen.py`/`reverse.py` ever need heavier refactoring.

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
