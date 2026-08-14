# Roadmap

Ideas that are in scope for this project but deliberately not started yet,
because they're a different size or a different kind of problem than the
current codebase. Unlike the "Pending" list in `README.md` (near-term,
already-scoped work), entries here need real design work before they're
ready to build.

## Reconstruct a YAML from a rendered video + an assets folder

**What's wanted:** given a finished/exported video file (not a `.kdenlive`
project — an actual `.mp4` or similar) and a folder of the source assets
that presumably went into it, produce a `kdgen`-compatible YAML that
recreates it: which asset appears where on the timeline, trimmed how, with
what fades.

**Why this is a different problem, not an extension of `reverse.py`:**
`kdgen import` today parses a `.kdenlive` file, which is a structured XML
document that already says explicitly "this producer, this in/out, this
filter" — it's a deterministic, lossless-where-recognized translation from
one structured format to another. There is no structure to parse in a
rendered video: it's a flat stream of pixels and audio samples. Recovering
"which source clip, trimmed how" from that requires *content-based
matching* — comparing the rendered video's frames/audio against the
candidate assets in the folder — which is a fundamentally different
technique stack:

- Segmenting the rendered video into candidate clips (scene-cut / shot
  boundary detection).
- Matching each segment against the asset pool: perceptual/frame hashing
  or audio fingerprinting rather than exact comparison, since the render
  will have re-encoded, resized, and recompressed everything relative to
  the sources.
- Finding precise in/out trim points via cross-correlation/alignment, not
  exact byte or timestamp matching.
- Detecting fades from pixel brightness / audio volume ramps instead of
  reading them off an explicit filter, like `reverse.py` does today.
- No guaranteed correct answer: unlike XML parsing, this is inherently
  heuristic (ambiguous matches, near-duplicate assets, clips that don't
  appear in the folder at all).

**Recommendation when this gets picked up:** don't bolt this onto
`reverse.py` — it doesn't share `Importer`'s XML-tree-walking logic at all.
It's closer to a separate tool/subcommand (something like `kdgen
reconstruct`) built on a fingerprinting/matching library, with its own
tests and its own tolerance-for-error model (this will never be as exact as
the `.kdenlive` import). Worth prototyping the matching step in isolation
(can we reliably match a rendered clip back to its source asset at all,
before worrying about the CLI or YAML shape) before committing to an
architecture.

**Status:** not started. Flagged 2026-08-14 — the original ask for a
"reverse converter" was for both this and the already-implemented
`.kdenlive` → YAML import; only the latter is done.
