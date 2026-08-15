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
- CLI: `kdgen build` / `validate` / `import` / `render` (build + render to
  video via a real `melt`, added 2026-08-15).
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
- **`kdgen reconstruct`** — **implemented** (2026-08-14, merged to
  `master` 2026-08-15): reconstructs a YAML from an
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
- **Titles/text clips** — **implemented** (2026-08-15): a new bin clip
  kind, `type: title`, with `text:` (required) and optional `font:`
  (default `Sans`), `font_size:` (default 48), `color:`/`background:`
  (RGBA lists). Generates a standalone `.kdenlivetitle` file (`titles.py`,
  written to `.kdgen_titles/<clip_id>.kdenlivetitle` next to the project
  YAML) and a `<producer mlt_service="kdenlivetitle">` referencing it -
  modeled directly on a real title clip found in a local kdenlive backup,
  not built by analogy (the producer tag is `<producer>`, not `<chain>`;
  the file format is a separate small XML kdenlive's title editor itself
  produces). Deliberately narrow scope: one line of text, one style, no
  animation, no multiple items - a title card/caption, not a replacement
  for kdenlive's title editor. Text centering/sizing is a rough
  average-glyph-width estimate (no font-metrics library as a dependency),
  with the font auto-shrinking first if that estimate would overflow the
  canvas - checked empirically against real renders, not just structurally
  (`tests/test_melt_validation.py::test_melt_renders_a_real_title_clip`
  confirms visible text, not just that a producer got written without
  errors).

  `reverse.py` recognizes titles on import too (`titles.parse_title_xml`,
  the inverse of the generator): reads the referenced `.kdenlivetitle`
  file back and reconstructs `text`/`font`/`font_size`/`color`/
  `background`, falling back to `x_kdenlive_raw` for anything richer than
  this generator's narrow scope (multiple items, animation, image/rect
  items) rather than guessing. Verified round-trip
  (`tests/test_reverse.py::test_round_trip_preserves_title`) - which also
  caught a real bug: `Importer.preregister_bin_order` registered every
  `main_bin` entry through the generic (non-title-aware) path before the
  timeline entry was ever processed, so by the time the title-aware code
  ran, the clip was already registered with no title data attached at
  all.

  Along the way, checking whether `reverse.py` also recognizes
  `transition: wipe` on import (it doesn't - and neither did the
  qtblend/mix compositing transitions every project already has, since
  sequence-level `<transition>` elements were never inspected at all)
  surfaced a real gap for wipe specifically: unlike those two, silently
  not reconstructing a wipe means silently *losing* it on the next build,
  not just failing to label it. Fixed by flagging any transition with a
  non-zero `a_track` (this generator's own compositing-against-black
  transitions are always `a_track="0"`) into `x_kdenlive_raw` instead of
  dropping it unnoticed.
- **Speed change (timewarp)** — **implemented** (2026-08-15): `speed:` on
  a clip placement (video or audio - not video-specific, per the
  `timewarp` MLT producer itself). `in:`/`out:`/`duration:` are relative
  to the *warped* length (source duration / speed), matching kdenlive's
  own convention. Optional `preserve_pitch: true` maps to `warp_pitch`
  (audio pitch-correction when the speed changes; off by default, same as
  dragging the speed slider in kdenlive without the "preserve pitch"
  checkbox). Modeled directly on a real user project with actual timewarp
  clips (found in a local kdenlive backup), not built by analogy - the
  `resource` property is `"{speed}:{path}"`, not just the path, and the
  producer tag is `<producer>`, not `<chain>` like every other clip this
  generator writes. Verified with a real render (not just that a
  `timewarp` producer got written, but that melt actually renders the
  warped duration):
  `tests/test_melt_validation.py::test_melt_respects_video_speed_change`
  (2x) and `::test_melt_respects_audio_speed_change` (0.5x). `reverse.py`
  recognizes it on import too (forward speed only - reverse/negative
  speed still falls to `x_kdenlive_raw`), verified round-trip
  (`tests/test_reverse.py::test_round_trip_preserves_speed`).
- **Transitions**, two parts:
  - **Verified** (2026-08-15): `transition: dissolve` in `scenes.py`,
    previously marked in its own docstring as never checked against a real
    kdenlive project (the same "best-effort" category that turned out to
    be wrong for both audio fades and nonzero `in:` trims earlier this
    cycle) — this one turned out correct. Confirmed with a real `melt`
    render: a red clip dissolving into a blue one produces a genuine,
    monotonic cross-fade through the transition window, not a hard cut or
    a fade through black
    (`tests/test_melt_validation.py::test_scenes_dissolve_produces_a_real_crossfade`).
    Needed a `melt` build with a working `qtblend` to verify at all — on
    this machine neither the system package nor kdenlive's own snap has
    one (see `conftest.py`'s `_find_melt`/`melt_with_qtblend`); a
    from-source MLT build with the Qt module compiled in was needed
    instead. No fix was needed, only verification.
  - **`transition: wipe`** — **implemented** (2026-08-15): a genuinely new
    mechanism, not a variant of `dissolve` - a wipe needs a real
    two-source MLT transition (`mlt_service: luma`) so a grayscale luma
    map can decide, per pixel, which of the two overlapping clips shows;
    a per-clip alpha filter (what `dissolve` uses) can't do that. Same
    alternating-track split as `dissolve`, but each overlap window
    becomes a real `<transition>` between the two tracks
    (`WipeTransitionSpec` in `models.py`) instead of a fade. `luma:` on
    the scene picks the shape (`linear_x`, `linear_y`, `diagonal`,
    `radial`, `square`, `clock`, `spiral`, `bars_horizontal`,
    `bars_vertical`, `checkerboard` - `lumas.py`/`lumas_data/`), default
    `linear_x`. These are procedurally generated by this project (plain
    gradient/angle/distance math), not copies of kdenlive's own luma
    maps: kdenlive's are GPL-licensed and this project doesn't declare a
    GPL-compatible license, so bundling them would be a real
    entanglement — a similar but not identical shape set instead.

    Verified with a real render, spatially (not just that *some* blend
    happens, like dissolve's check - a wipe specifically needs to prove
    the reveal sweeps across the frame):
    `tests/test_melt_validation.py::test_scenes_wipe_produces_a_real_spatial_wipe`
    samples individual columns and confirms the left edge can already be
    blue while the right edge is still red.

    Surfaced two real bugs along the way, both fixed:
    - MLT's `luma` transition reads `.pgm` luma maps directly; anything
      else (including plain `.png`) goes through a generic image-loader
      producer path that empirically produced a near-instant cut instead
      of a gradual wipe for the same gradient. Lumas are shipped as tiny
      (64x64) `.pgm` files now, not `.png`.
    - An MLT tractor applies its transitions in declaration order. Once a
      qtblend compositing transition had already run against a track, a
      *later* `luma` transition between two *other* tracks stopped
      progressing over time entirely (every sampled frame landed on the
      same side of the wipe, as if `in`/`out` were being ignored).
      Emitting the wipe transitions before the qtblend ones in
      `xmlgen.py` fixed it - root cause not fully understood, but the fix
      was verified empirically and is documented as such in code.
