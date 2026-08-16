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

## v0.2 — shipped (2026-08-15)

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

## v0.3 — shipped (2026-08-15)

Three groups, all **implemented** (2026-08-15): quick fixes (low risk,
bundled housekeeping), symmetry closes (mirror a pattern a v0.2 feature
already shipped), and one new transition mechanism as the headline
feature.

- **Quick fixes** — **implemented**:
  - `font_size`/`colorspace` range validation in `schema.py` (previously
    unchecked, unlike the sibling `speed_not_positive` check it now
    replaces - see below).
  - PiP `position`/`size` bounds-checking against the project canvas in
    `schema.py` (previously only checked shape, not that the rect stays
    within `width`/`height`).
  - `cli.py` output-write errors (`xmlgen.write`, `output.write_text` in
    `cmd_build`/`cmd_import`/`cmd_reconstruct`) now routed through the
    same `KdgenError`/`msg()` path every other failure mode already
    uses, instead of a raw traceback.
  - `--lang` CLI flag as an alternative to the `KDGEN_LANG` environment
    variable - applied before `build_parser()` (a lightweight pre-parser
    peels it off `argv`) so `--help` itself comes out localized too.
  - `pyproject.toml` version bump to match what's actually shipped.
  - `examples/simple/project.yaml` now also demonstrates `speed:`,
    `mute:`, and `type: title`; a new `examples/scenes/project.yaml`
    demonstrates the `scenes:` compiler itself (a cut, a `dissolve`, and
    a `wipe`) - previously no example touched any of v0.2's features.
- **Reverse speed (negative timewarp)** — **implemented**: `speed:` now
  accepts negative values (0 still rejected - `validation.speed_zero`
  replaces the old `speed_not_positive` check) on the generate side;
  `reverse.py` recognizes negative `warp_speed` on import instead of
  falling back to `x_kdenlive_raw`. Mirrors the forward-speed work
  shipped in v0.2 - same `resource` convention
  (`"{speed}:{path}"`), MLT's `timewarp` producer already handles the
  sign. Verified with a real render (a red-then-blue clip actually plays
  blue-then-red at `speed: -1.0`,
  `tests/test_melt_validation.py::test_melt_respects_reverse_speed_change`)
  and round-trip
  (`tests/test_reverse.py::test_round_trip_preserves_negative_speed`).
- **Audio pan/balance** — **implemented**: `pan:` (-1.0..1.0) on an
  audio track makes the already-emitted `panner` filter (`channel=-1`,
  stereo balance) a real per-track control instead of the hardcoded
  `disable=1` mixer-strip placeholder every track already had - same
  shape as `mute`, one already-plumbed filter, no new mechanism.
  Audio-only, range-checked. Verified with a real render: full-left pan
  renders much louder on the left channel than the right
  (`tests/test_melt_validation.py::test_melt_applies_a_real_audio_pan`) -
  which also confirmed, as a side effect, that this project's stereo
  profile upmixes a mono source before the panner filter runs.
- **`scenes:` ergonomics** — **implemented**:
  - A scene clip entry can now be `{clip: id, mute: true}` or
    `{path: '...', speed: 2.0}` - the same `mute`/`speed`/
    `preserve_pitch`/`position`/`size` fields `timeline.tracks[].clips[]`
    already supports at the low-level, so reaching them no longer forces
    dropping out of `scenes:` down to hand-written tracks
    (`scenes.py`'s `extract_clip_extras`). Fixed a real interaction bug
    this surfaced along the way: the auto-pacing `video_cap` compared a
    sped-up clip against its *unwarped* source duration, so it could
    compute an `out:` beyond what the clip actually has left on the
    warped timeline.
  - A scene whose clips are *all* audio now routes to a separate
    audio-typed alternating track pair ("Scenes A (audio)"/"Scenes B
    (audio)") instead of being forced onto `scenes.py`'s previously
    video-only tracks. `transition: wipe`/`affine` have no audio
    equivalent and are now a validation error there instead of silently
    misapplying. Verified with a real render, not just structurally:
    two different tones (220Hz/880Hz) genuinely crossfade
    (`tests/test_melt_validation.py::test_scenes_audio_dissolve_produces_a_real_crossfade`)
    — which caught a real mechanism difference from the video case: MLT's
    audio `mix` transition *sums* both tracks (not `qtblend`'s alpha
    compositing), so an audio dissolve needs *both* clips to fade
    complementarily, not just the "top" one as a video dissolve does, or
    the un-faded clip plays at full volume and stops dead at its own
    boundary - an audible glitch, not a crossfade.
- **New transition: `affine`-based `zoom`/`slide`** — **implemented**,
  **scope trimmed from the original plan**: a third transition engine
  alongside `dissolve`'s alpha fade and `wipe`'s luma map, built on
  MLT's own `affine` transition (Meltytech, LGPLv2.1, already shipped
  with MLT - no new dependency). `transition: affine` + `style: zoom`
  (default, incoming clip's rect grows from a tiny centered point to the
  full frame) or `style: slide` (incoming clip's rect slides in from
  fully off-frame right). Same alternating-track-overlap shape
  `dissolve`/`wipe` already use; neither clip gets a fade, the
  transition alone decides visibility, like `wipe`.

  Originally planned as "zoom/spin/cube/push-slide" per the affine
  transition's own documented `rotate_x/y/z`/`fix_rotate_x/y/z`
  parameters. **`spin`/`cube` (rotation) were investigated and dropped**:
  empirically, on this project's real melt build, those properties are
  accepted without error but never actually rotate pixels - only shrink
  the destination rect's bounding box (checked directly by rendering a
  full-frame rect at various fixed and keyframed rotation angles and
  sampling its corners: a real rotation should expose background there,
  and never did, with `fill`/`distort`/`rotate_center` toggled every way
  tried). Not shipped rather than shipping something unverified - see
  BACKLOG.md for `frei0r.3dflippo`, found afterward via separate
  research and *verified* by real render to produce a genuine
  perspective-correct 3D flip/cube effect through a different mechanism
  entirely (a single-clip filter, not `affine`'s rotation properties),
  a strong candidate to actually deliver on the original "cube" ambition
  next.

  `zoom` and `slide` are both verified with real renders, not just
  structurally: `zoom` against a center-vs-edge pixel sample (center
  already reveals the incoming clip while the edge doesn't, the opposite
  spatial signature from `wipe`'s left-to-right sweep) -
  `tests/test_melt_validation.py::test_scenes_affine_zoom_produces_a_real_zoom`;
  `slide` against the same column-sampling technique `wipe`'s own test
  uses -
  `tests/test_melt_validation.py::test_scenes_affine_slide_produces_a_real_slide`.

  Also surfaced and confirmed a real, easy-to-miss `affine` `rect`
  syntax trap along the way: mixing absolute-pixel and percentage units
  in the same rect string (`"320/0:100%x100%:100"`) silently produces a
  no-op/wrong result instead of an error - every field has to use the
  same unit (all percentages, in this project's case), matching what the
  MLT docs say but easy to violate by accident since nothing rejects the
  mix.

## v0.4 — shipped (2026-08-15)

Four groups: two new transitions as the headline (both already verified
by real render during backlog research, moved here from BACKLOG.md
rather than re-investigated from scratch), two quick fixes, a symmetry
close for `reverse.py`, and a project/track metadata round-trip theme.

- **New transitions: `iris` and `flip`** — **implemented**: unlike
  `dissolve`/`wipe`/`affine`, each gets its own `transition:` value
  (`iris`/`flip`) rather than being a `style:` of another transition -
  both are **single-clip filters** composited via the `qtblend` this
  project already emits per video track, not a genuine two-source MLT
  `<transition>` like `wipe`/`affine` (same mechanism `dissolve` itself
  uses: only the alternating "top" clip needs the filter, the "bottom"
  one stays untouched and shows through wherever the top's alpha is
  transparent). No new `WipeTransitionSpec`-like model needed - just a
  new `ResolvedPlacement.reveal_style` field
  (`_add_reveal_style_filter` in `xmlgen.py`, replacing the plain
  brightness fade when set).
  - **`iris`**: a shape grows from a tiny center point into a
    full-frame reveal with a soft edge, via `frei0r.alphaspot` (shape
    param `0` = `1` for a circle/ellipse - **correcting** an earlier
    backlog note: values below ~0.3 render as a plain rectangle despite
    silently accepting any 0..1 float, `>=0.15` was wrong; size params
    `3`/`4` keyframed `0.02` -> `1.0`; transition-width param `6` for
    the soft edge).
  - **`flip`**: a genuine 3D rotation via `frei0r.3dflippo` (Y-axis
    param `1`, keyframed `0.92` -> `0.5` entering / `0.5` -> `0.92`
    exiting - narrower than the full `1.0`..`0.5` range because the
    reveal is barely visible near full edge-on due to perspective
    foreshortening, confirmed empirically: most of the width change
    happens near the *end* of the animation, not linearly across it).
    Needs `"Fill with image or black"` (param `10`) set to `1` for the
    rotated-away area to be **real transparency** revealing the track
    below - `0` doesn't give transparency at all, it shows that same
    clip's own unrotated pixels underneath instead, and against a plain
    black background the two look identical without a real render (with
    a colored layer underneath) to tell them apart. This was checked
    directly, not assumed from the earlier backlog research.

  Both verified with real melt renders through the actual generator
  output, not a standalone probe: `iris` against a center-vs-edge pixel
  sample (same spatial signature as `affine`'s `zoom`) -
  `tests/test_melt_validation.py::test_scenes_iris_produces_a_real_iris_reveal`;
  `flip` against the same left-to-right column-sampling technique
  `wipe`'s own test uses -
  `tests/test_melt_validation.py::test_scenes_flip_produces_a_real_spatial_sweep`.
- **Quick fixes**:
  - **Guides/markers**: `xmlgen.py` hardcodes
    `kdenlive:sequenceproperties.guides` to `"[]"`; wire a YAML
    `guides:` list into that JSON - the placeholder already exists in
    the emitted XML.
  - **`--keep-kdenlive`/`-k` for `kdgen render`**: it currently builds
    into a `tempfile.TemporaryDirectory()` and discards the
    intermediate `.kdenlive` once `melt` finishes, with no way to keep
    the file it just generated.
- **Cross-track transitions reconstructable** — **implemented, scope
  narrowed from "into `scenes:`" to the low-level model**: reconstructing
  the original `scenes:` sugar (which clips, in what order, `pacing`/
  `duration`) from an arbitrary sequence would need real pattern
  recognition - detecting that two tracks' clips alternate with
  transitions at exactly the boundaries `scenes.py` itself would have
  produced - a much bigger task than "don't lose the data." Instead,
  `wipe`/`affine` cross-track `<transition>`s this generator itself
  could have produced are now recognized on import and written back as a
  new top-level `transitions:` YAML key - the low-level, `scenes:`-free
  way to declare one directly between two named `timeline.tracks:`
  (`schema.py`'s `_parse_transition`, resolved into the same
  `WipeTransitionSpec`/`AffineTransitionSpec` `scenes.py` already
  produces, so `xmlgen.py` needed no changes at all to consume either
  source). Recognition is conservative, matching this project's "never
  guess" bar: a `luma` transition's resource has to match one of this
  project's own lumas by filename, an `affine` transition's `rect` has
  to match the exact zoom/slide keyframe template `xmlgen.py` itself
  emits (reusing `_AFFINE_STYLE_RECT`, the same table that generates
  them) - anything else still falls back to `x_kdenlive_raw`, unchanged.
  `iris`/`flip` aren't covered by this (they're single-clip filters, not
  `<transition>` elements - a different, unaddressed gap, see
  BACKLOG.md). Verified with a real generated .kdenlive round-tripping
  back through this exact path, not just structurally: both `wipe` and
  `affine` (`test_round_trip_preserves_wipe_transition`,
  `test_round_trip_preserves_affine_transition`), including that the
  reconstructed YAML is itself re-buildable (validates against this
  project's own schema, not just "looks right").
- **Project/track metadata round-trip** — **implemented**: every field
  in this group is only *written* when set (a fresh kdenlive project
  itself doesn't write most of these properties at all until a user
  actually touches the corresponding UI control - confirmed against
  kdenlive's own source for each, not modeled by analogy), and every
  one round-trips on import.
  - **Track names**: `name:` on a track now writes
    `kdenlive:track_name` on the track's own tractor
    (`TrackModel::TrackModel`, `src/timeline2/model/trackmodel.cpp`);
    `reverse.py` reads it back instead of always falling to the
    `V1`/`A1` auto-generated fallback.
  - **Hidden/locked track state**: new `hidden:`/`locked:` on a track
    write `hide` (a bitmask on the track's own tractor - bit 1 = hidden,
    bit 2 = muted, though only the `hidden:` bit is exposed for now,
    matching the ROADMAP's original narrower scope) and
    `kdenlive:locked_track` (`TrackModel::isHidden`/`isMute`/
    `isLocked`). Verified with a real render, not assumed from the
    source alone - a genuine surprise along the way: `hidden:` actually
    blacks out the track in melt's rendered output, not just kdenlive's
    own editor UI (`test_melt_hides_a_hidden_track`); `locked:` has no
    such render-time effect, purely an editor-lock toggle.
  - **Bin folder organization**: new `folder:` on a bin clip assigns a
    deterministic folder id (sorted by name, starting at 3 - id 2 is
    always the "Secuencias" folder every project already gets) and
    writes `kdenlive:folder.-1.<id>` on `main_bin`
    (`BinPlaylist::manageBinItemInsertion`,
    `src/bin/binplaylist.cpp`) plus a non-default `kdenlive:folderid`
    on that clip's *master* bin chain only (the per-track embedded
    copies stay `-1` - folder membership is a Project Bin concept, not
    a timeline one). `reverse.py` reads `main_bin`'s own
    `kdenlive:folder.-1.<id>` properties back into `folder:` on each
    bin entry.
  - **Project-level notes**: new `notes:` on the project writes
    `kdenlive:documentnotes` on `main_bin`
    (`ProjectManager::slotSaveProject`,
    `src/project/projectmanager.cpp`) - **not** paired with an
    `author:` field as originally scoped: kdenlive has no author/creator
    concept anywhere in its own source (checked directly, not assumed),
    so that half of the original item was dropped as not real rather
    than invented.

## v0.5 — shipped (2026-08-15)

Four groups: symmetry closes (reverse.py recognizing what v0.3/v0.4
already generate), a new filter capability as the headline, two more
transitions extending the dissolve/wipe/affine/iris/flip family, and one
more small filter capability.

- **Symmetry closes**:
  - **`pan:` recognized on import**: `reverse.py` only ever reads
    per-entry filters; it never inspects a track tractor's own filters,
    which is where the `panner` filter `pan:` controls actually live.
    A project with a real balance/pan setting round-trips back
    `pan`-less today.
  - **`iris`/`flip` recognized on import**: both are single-clip filters
    (`frei0r.alphaspot`/`frei0r.3dflippo`), not `<transition>` elements,
    so v0.4's cross-track-transition recognition can't see them at all.
    `convert_clip_filter` needs to recognize both services and translate
    their keyframed size/rotation back into `style: iris`/`style: flip`
    scene-clip markers (or fall to `x_kdenlive_raw` if the keyframes
    don't match what `xmlgen.py` itself would produce, same
    conservative bar `wipe`/`affine` recognition already holds to).
  - **Track-level audio mute**: `hide`'s bit 2 (`TrackModel::isMute`)
    is a whole-track audio mute, distinct from the per-*clip* `mute:`
    already supported - confirmed to exist in kdenlive's own source
    during v0.4, deliberately left unexposed then to keep that item's
    scope narrow. Same shape as `hidden:`, just the other bit - needs a
    distinct name (not `mute:`, already taken at the clip level).
- **Chroma-key / green-screen compositing** — **implemented**, the
  headline feature, **mechanism changed from what was planned**:
  `avfilter.chromakey` (the originally planned filter) produced a real
  "Cannot set src colorspace" warning and **no visible keying effect at
  all** on this project's real melt build - the same failure
  `avfilter.rgbashift` had (see `glitch`'s entry). `frei0r.bluescreen0r`
  was used instead - kdenlive's own actual mechanism for this (its UI
  ships this exact filter) - verified by real render to cleanly key a
  solid-color background to transparency (revealing the track below)
  while leaving a differently-colored subject untouched, confirmed with
  two different key colors (green and blue) before wiring it in.

  New `chroma_key:`/`chroma_key_distance:` fields directly on a clip
  placement (`timeline.tracks[].clips[]`, and as a `scenes:`-clip
  extra alongside `mute:`/`speed:`/`position:`) - unlike
  `reveal_style`, this spans the clip's *whole* duration (no
  fade_in/fade_out window, no keyframes) and needs no second-track/
  transition setup of its own: it composites through the same
  per-track `qtblend` chain every video track already gets against the
  ones below it, so a user just needs a background clip on a lower
  track and a `chroma_key:` clip on a track above it. Recognized on
  import too - unlike `reveal:`'s exact-keyframe-match requirement,
  color/distance are plain user-facing values, so any value round-trips
  (only `Invert`, unsupported, still falls back to `x_kdenlive_raw`).
- **New transition: `glitch`** — **implemented**, **`broken-tv` dropped
  after verification**: both were investigated first, same discipline
  that dropped `affine`'s `spin`/`cube` from v0.3.
  - `frei0r.nosync0r` (the intended mechanism for `broken-tv`) produces
    **no visible effect** on this project's real melt build - a
    rendered frame differs from its unfiltered baseline by a mean pixel
    difference of ~1/255, imperceptible, even at the parameter's
    maximum value. Not shipped.
  - `avfilter.rgbashift` (the intended chromatic-aberration companion
    for `glitch`) also produced no visible shift, paired with a
    "Cannot set src colorspace" warning - dropped too. `glitch` uses
    `frei0r.glitch0r` alone, which *is* clearly working (real,
    distinctive block-shifting, verified by render).
  - **Mechanism turned out different from what was planned**: `glitch0r`
    writes no alpha channel at all (unlike `iris`/`flip`'s
    `alphaspot`/`3dflippo`), so the alternating-two-track qtblend-reveal
    shape those use doesn't apply here - a fully-opaque "top" clip would
    just obscure the bottom one for the whole overlap, pointlessly.
    `glitch` clips instead stay on a single dedicated track
    (`scenes.py`'s `GLITCH_TRACK_NAME`), placed back-to-back with no
    real time overlap - `transition_duration` means "how long the burst
    dressing each cut lasts," not an overlap. Reuses the same
    `reveal_style`/`fade_in`/`fade_out` per-clip mechanism `iris`/`flip`
    introduced in v0.4, just applied to *every* clip's own edges instead
    of an alternating "top" one.

  Verified with a real melt render: a solid-color clip shows much
  higher pixel variance during the glitch burst window than outside it
  (`test_scenes_glitch_produces_real_block_shifting`) - and recognized
  on import too (`reverse.py`'s `_match_reveal_filter`, same
  conservative exact-parameter-match bar `iris`/`flip` recognition
  already holds to), round-tripping back to `reveal: glitch`.
- **`dynamictext` filter (burned-in timecode/date overlay)** —
  **implemented**: MLT's own `dynamictext` filter (native, not
  avfilter-bridged - no colorspace issues like `chroma_key`/`glitch`
  ran into) overlays text as a filter *on any existing clip*, with
  keyword substitution (`#timecode#`, `#frame#`, `#localtime#`...
  passed through as-is) - kdenlive itself exposes it as "Dynamic Text".
  Distinct from `titles.py`, which only produces standalone `type:
  title` bin clips, not a text-overlay filter on existing footage.

  New `overlay_text:`/`overlay_font:`/`overlay_font_size:`/
  `overlay_color:`/`overlay_background:` fields directly on a clip
  placement, reusing `titles.py`'s own field-naming convention and
  defaults (`Sans`, `48`, white, transparent). Verified by real render:
  white `#timecode#` text is clearly visible against a black test clip
  (`test_melt_renders_a_real_text_overlay`, same rigor
  `test_melt_renders_a_real_title_clip` got). Recognized on import too,
  same "plain values round-trip freely" bar `chroma_key:` uses - except
  the fixed centered/full-frame layout this generator always writes has
  to match exactly, since `geometry:` itself isn't a settable field.

## v0.6 — scoped (2026-08-16)

Two blocks: a headline of three mechanisms already verified by real
render during backlog research (moved here from BACKLOG.md, not
re-investigated from scratch - same pattern as v0.4's `delaygrab`/
`cairogradient` write-up), and a second block of two kdenlive-native
effects that still need their own verification pass before
implementation, same discipline that dropped `affine`'s `spin`/`cube`
(v0.3), `broken-tv` (v0.5), and `avfilter.chromakey`/`rgbashift`
(v0.5) after they failed it.

- **Ken Burns pan+zoom, single-clip filter** — headline,
  **implemented**: `affine` used as a lone filter (not a transition,
  unlike `zoom`/`slide`) with a keyframed `transition.rect`, animating a
  smooth pan+zoom over the clip's duration. Cross-checked directly
  against kdenlive's own effect definition (`~/dev/kdenlive/data/
  effects/pan_zoom.xml`): kdenlive's real "Position and Zoom" effect is
  literally the same `tag="affine"` mechanism with a `transition.rect`
  animatedrect parameter, not modeled by analogy. **One correction from
  the backlog note**: the property has to be prefixed `transition.rect`,
  not the bare `rect` the affine *transition* (`zoom`/`slide`) uses
  directly - confirmed via `melt -query filter=affine` (its own
  parameter schema lists `transition.*` as "the primary means to use
  this filter") and by render: a bare `rect` on the filter produced no
  animation at all, while `transition.rect` did.

  New `ken_burns_from:`/`ken_burns_to:` fields on `timeline.tracks[].
  clips[]`, each `[x%, y%, w%, h%]` - flat paired fields, matching this
  schema's existing `position:`/`size:` convention rather than a nested
  dict, since that's the established shape for a paired rect in this
  project. Not exposed as a `scenes:`-clip extra (same narrower-scope
  call v0.5's `overlay_text:` made, rather than chroma_key's wider one).
  Verified by real render through the generator's own output: a 39px
  test square (100%-rect start) grew to 59px (150%-rect end, 1.51x)
  while staying centered
  (`tests/test_melt_validation.py::test_ken_burns_produces_a_real_pan_zoom`).
  `reverse.py` recognizes it on import too, same "plain values round-trip
  freely" bar `chroma_key:` uses - except the two-keyframe/percent-units/
  opacity-100/no-distort shape `_add_ken_burns_filter` always writes has
  to match exactly, since that structure isn't a settable field
  (`tests/test_reverse.py::test_round_trip_preserves_ken_burns`).
- **New transition: `delaygrab`** — headline. `frei0r.delaygrab`
  ("delayed frame blitting on a time bitmap") already verified by real
  render: against a moving test source it produces a genuine circular
  vignette mask plus a visible temporal ghosting/trailing streak behind
  moving elements (a diagonal color bar left a clear smeared trail; an
  on-screen timestamp overlay dragged a comet-like tail). Exact
  transition shape (single-clip filter like `iris`/`flip`/`glitch`, or
  a genuine two-source `<transition>` like `wipe`/`affine`) still to be
  confirmed against the generator's own output during implementation,
  not assumed from the standalone probe render.
- **New transition: `cairogradient`** — headline. `frei0r.cairogradient`
  already verified by real render: an orange-to-transparent linear
  gradient (start/end color+opacity+position params) produces a genuine
  diagonal color-wash tint across the frame, visibly warmer on one
  side - a light-leak/gradient-overlay effect. Same caveat as
  `delaygrab` on exact mechanism shape, to be confirmed during
  implementation.
- **`pillar_echo` focus-pull effect** — second block, **needs
  verification before implementation**. Kdenlive's own real effect
  (`~/dev/kdenlive/data/effects/pillar_echo.xml`, credited to
  Meltytech itself - MLT-native, not frei0r), confirmed present via
  `melt -query filters` but not yet rendered: keeps a rectangular area
  of interest sharp while blurring everything outside it. If it doesn't
  hold up under a real render (no visible effect, or the effect doesn't
  match its documented behavior), drop it the same way `broken-tv`/
  `spin`/`cube` were dropped, and note that honestly here rather than
  shipping it unverified.
- **`dance` audio-reactive visualizer** — second block, **needs
  verification before implementation**. Kdenlive's own real effect
  (`~/dev/kdenlive/data/effects/dance.xml`), confirmed present via
  `melt -query filters` but not yet rendered: moves/zooms the image
  proportional to the audio spectrum's magnitude (params: frequency
  band, level threshold, oscillation rate, initial zoom). Same
  verify-first bar as `pillar_echo` above - drop and document if it
  doesn't hold up.

**Explicitly deferred, not in v0.6**: speed ramps/`timeremap` (mechanism
confirmed real in BACKLOG.md but the exact working XML recipe isn't
nailed down yet - needs its own verification pass, not a fit for a
version whose headline is already three separate new mechanisms);
`mask_apply`/`mask_start` family and the `heart`/`bowtie` luma shapes
(leads/sketches only, unverified); subtitles, proxy generation, generic
color grading (`lift_gamma_gain`), richer title clips (large standalone
lifts with no natural pairing here); freeze-frame, stabilization,
motion tracking, LUT-based grading (`avfilter.lut3d`), audio ducking
(reserved for a later "kdenlive-native/competitor-parity effects"
theme, together with whichever of `pillar_echo`/`dance` doesn't ship
here).
