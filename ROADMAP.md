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
- CLI: `video-as-code build` / `validate` / `import` / `render` (build + render to
  video via a real `melt`, added 2026-08-15).
- i18n message catalog for user-facing output (`en`/`es`, via `VIDEO_AS_CODE_LANG`).
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
- **`video-as-code reconstruct`** — **implemented** (2026-08-14, merged to
  `master` 2026-08-15): reconstructs a YAML from an
  already-rendered video + an assets folder, via content-based matching
  (not XML parsing — there's no structure to walk, just pixels and audio
  samples). Its own module (`reconstruct.py`) and CLI subcommand
  (`video-as-code reconstruct <video> <assets_dir> [-o output.yaml]`), not an
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
  written to `.titles/<clip_id>.kdenlivetitle` next to the project
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
    same `KdenliveAsCodeError`/`msg()` path every other failure mode already
    uses, instead of a raw traceback.
  - `--lang` CLI flag as an alternative to the `VIDEO_AS_CODE_LANG` environment
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
  - **`--keep-kdenlive`/`-k` for `video-as-code render`**: it currently builds
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

## v0.6 — shipped (2026-08-16)

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
- **New transitions: `delaygrab` and `cairogradient`** — headline,
  **implemented**, **mechanism shape resolved: both are `glitch`-style
  single-track filter bursts, not two-source `<transition>`s or
  alpha-reveal filters like `iris`/`flip`**. Confirmed by real render
  before wiring either in: a colored background on the track below
  never shows through a `frei0r.delaygrab`- or `frei0r.cairogradient`-
  filtered clip (unlike `alphaspot`/`3dflippo`, neither writes any
  alpha at all), so `BURST_TRANSITIONS` (`scenes.py`) generalizes what
  was `glitch`'s own dedicated single-track/no-overlap mechanism
  (renamed `GLITCH_TRACK_NAME` -> `BURST_TRACK_NAME`, now shared by all
  three) instead of introducing a new one.
  - **`delaygrab`**: verified by real render - a moving element
    crossing the filter's (fixed-position, default-sized) sample region
    leaves a stale/delayed ghost there for a brief window before the
    delay buffer catches back up to live content (diffed against an
    unfiltered baseline: a strong, localized difference early in the
    burst window, fading to near-zero once the moving element has
    passed through). Exposes no adjustable parameters at all - neither
    does kdenlive's own effect definition for it
    (`data/effects/frei0r/frei0r_delaygrab.xml`), and `melt -query
    filter=frei0r.delaygrab` doesn't list an introspectable parameter
    set either - used with its plugin defaults, not modeled by analogy.
  - **`cairogradient`**: verified by real render - a diagonal
    orange-to-transparent gradient (start/end color+opacity+position
    params) blended directly onto the clip's own pixels (checked with a
    green clip over a blue background: the composited result never
    shows any blue, confirming a tint on the top clip itself, not a
    reveal).

  Both verified through the generator's own output, not the standalone
  probe renders alone: `delaygrab` by comparing the same scene rendered
  with `transition: delaygrab` vs `transition: none` at the same
  instant - a real burst renders visibly differently from a plain cut
  (`tests/test_melt_validation.py::test_scenes_delaygrab_produces_visible_temporal_ghosting`);
  `cairogradient` against a whole-frame pixel-variance check, same
  technique `glitch`'s own test uses
  (`tests/test_melt_validation.py::test_scenes_cairogradient_produces_a_visible_color_wash`)
  - which surfaced a real test-methodology bug along the way: summing
  variance across R, G, *and* B (not just R) is needed in general,
  since a gradient color choice can happen to share one channel's value
  with the base color (here, orange `#ff8800` on pure red - both ~255
  in R, so the real variance was entirely in G) and look like zero
  effect on a single channel alone.

  Recognized on import too (`reverse.py`), same conservative
  exact-parameter-match bar `glitch` recognition already holds to
  (`_match_reveal_filter`) - `delaygrab` only needs to match the
  service name (no parameters to check), `cairogradient` matches its
  fixed gradient parameters exactly.
- **`pillar_echo` focus-pull effect** — second block, **implemented**.
  Kdenlive's own real effect (`~/dev/kdenlive/data/effects/
  pillar_echo.xml`, credited to Meltytech itself - MLT-native, not
  frei0r): keeps a rectangular area of interest sharp while blurring
  everything outside it. Verified by real render, through the
  generator's own output, not just the standalone probe: a fine
  checkerboard test pattern measured a pixel-value stddev of ~127
  inside the rect vs. ~5-7 just outside it - a real, strong blur
  (`tests/test_melt_validation.py::test_focus_rect_produces_a_real_blur_outside_the_rect`).

  New `focus_rect:`/`focus_blur:` fields on `timeline.tracks[].clips[]`
  (`focus_rect` is `[x%, y%, w%, h%]`, `focus_blur` percent of image
  width, default 4.0 matching kdenlive's own default) - spans the
  clip's whole duration, no keyframes (kdenlive's own `rect` param
  supports keyframing but that's not exposed here, same narrower-scope
  call `overlay_text:`'s fixed layout made). Recognized on import too,
  same "plain values round-trip freely" bar `chroma_key:` uses.
- **`dance` audio-reactive visualizer — investigated and dropped**,
  same discipline that dropped `broken-tv`/`spin`/`cube`/
  `avfilter.chromakey`/`rgbashift`, but a different *kind* of failure
  than any of those: `dance` itself works, verified by a real render
  in complete isolation (a static marker visibly shifted position only
  during a loud in-band tone, snapping back the instant the audio went
  silent) - but it doesn't work *through this generator's own output*,
  and the reason is structural, not a parameter mistake. Every video
  track this generator (and real kdenlive - confirmed against actual
  `.kdenlive` projects on this machine, not just this project's own
  output) emits sets `hide="audio"` on that track's own inner
  sub-tracks; tracing MLT's own tractor source
  (`src/framework/mlt_tractor.c`) confirmed that flag doesn't just
  reorder audio processing, it excludes the track from ever being
  picked as an audio contributor at all - the frame `dance`'s filter
  receives has no live audio connection whatsoever to hook into,
  hence its own "Audio not preprocessed. Unable to dance." warning.
  This is exactly *why* the video-track `mute:` independent-fades
  trick from v0.2 works and is necessary in the first place - video
  tracks never carry their own audio through to render at all,
  matching real kdenlive's actual per-clip AV-split editing model,
  confirmed by checking a real, separately-edited `.kdenlive` project
  on this machine. Making `dance` work would need reaching audio from
  the *project-level* mix instead of a per-clip filter - a
  fundamentally different mechanism shape (a project-wide effect, not
  a `timeline.tracks[].clips[]` field like everything else in this
  schema), out of scope for this cycle.

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

## v0.7 — shipped (2026-08-17)

Headline: speed ramps, a genuinely blocked feature finally unblocked
this session - deferred at both the v0.6 and (implicitly) earlier
scoping rounds as "needs one more verification pass," which this round
delivered. Two quick wins alongside it, both small, well-understood
lifts from BACKLOG.md, plus `source_in:`/`source_out:` added mid-cycle.
`Freeze-frame / hold`, the third originally-scoped quick win, wasn't
reached this cycle - moved back to BACKLOG.md rather than held open,
since `crop`'s workaround made this a natural closing point.

- **Speed ramps (`timeremap`)** — headline, **implemented**. MLT's
  `timeremap` **link** (not a plain producer property like `timewarp`)
  attached to a `<chain>` - matches kdenlive's own construction exactly
  (`~/dev/kdenlive/src/bin/projectclip.cpp`: `Mlt::Link
  link("timeremap"); ... chain->attach(link);`), including the real
  XML serialization shape (`id` attribute + nested `<property>`
  children - not `mlt_service` as an XML attribute directly on
  `<link>`, which also happens to parse but doesn't match what
  kdenlive itself writes, confirmed against MLT's own XML writer,
  `~/dev/mlt/src/modules/xml/consumer_xml.c`'s `serialise_link`).

  **The actual blocker, found and fixed this session**: `time_map`'s
  keyframe *keys* are frames, but its *values* are **seconds**, not
  frames - confirmed directly in kdenlive's own source
  (`~/dev/kdenlive/src/utils/gentime.h`: `GenTime(double seconds)`,
  the exact constructor kdenlive wraps every `time_map` read/write
  through). The earlier attempt used frame numbers for both, which
  silently produced a wildly-wrong, clamped mapping instead of an
  error - exactly the kind of trap this project's verification
  discipline exists to catch before it ships.

  **Second finding**: a sparse, 2-keyframe linear map (start and end
  only) under-shot the target - the render never reached the final
  color, landing roughly 75% of the way through the intended range
  despite the endpoints being mathematically correct for a linear
  interpolation. Root cause not fully understood, but a *dense*
  keyframe map (one keyframe per output frame, not just per ramp
  breakpoint) reliably hit the correct position throughout - the
  implementation (`xmlgen.py`'s `_speed_ramp_time_map`) emits dense
  keyframes as a documented, verified workaround, not assuming sparse
  ones are safe.

  New `speed_ramp:` field on `timeline.tracks[].clips[]`: a list of
  `{at: seconds, speed: multiplier}` keyframes, each marking where a
  new *constant*-speed segment starts (speed holds flat until the next
  keyframe, or the clip's own end for the last one) - several flat
  segments in sequence, not a smoothly-eased curve; mutually exclusive
  with `speed:`. Deliberately narrow scope: always starts at the
  source's own beginning (`in:` isn't supported alongside it - a
  dedicated validation error, not silently ignored), and needs an
  explicit `out:`/`duration:` (no auto-inferred "use all available
  source" default like flat `speed:` gets, since that would need
  solving for where a multi-segment ramp runs out of source material
  rather than a single division). Validated against the bin clip's
  real source duration by walking the ramp's own segments and summing
  consumed source time - a ramp that would need more material than
  exists is rejected at validation time, not silently clamped.
  `speed_ramp_image_mode:` (`nearest`/`blend`, default `nearest`
  matching kdenlive) exposes `timeremap`'s own `image_mode`; pitch
  compensation reuses the existing `preserve_pitch:` field.

  Verified by real render through the generator's own output, not
  just the standalone probe used to find the mechanism: a 4-second
  red/green/blue/yellow test source (1s each) ramped at a flat 2x
  played all four colors in order within the expected 2s output window
  (`tests/test_melt_validation.py::test_melt_respects_speed_ramp`).
  Recognized as unsupported on import (`reverse.py`) rather than
  silently lost - reconstructing the original `{at, speed}` breakpoints
  from a dense, per-frame `time_map` isn't attempted (same "never
  guess" bar as anything else this generator can't cleanly invert), so
  it falls to `x_kdenlive_raw` instead. This closes a real gap
  `classify_producer` had: it only ever read a chain's own
  `resource`/`mlt_service`, never a child `<link>`, so a ramped clip
  would otherwise have silently come back as an ordinary unramped one
  on re-import instead of being flagged
  (`tests/test_reverse.py::test_speed_ramp_falls_to_raw_items_instead_of_silently_lost`).
- **Crop — implemented, with a workaround** for a real bug in this
  project's own preferred test build. The mechanism is MLT's own `crop`
  filter, matching kdenlive's "Edge Crop" effect exactly, and (as first
  found) needs **two** filter instances on the same clip: one with
  `left`/`right`/`top`/`bottom` and `active` left at its default `0`
  (this one only *computes* the crop and stores it as
  `crop.left`/`crop.right`/`crop.top`/`crop.bottom` properties on the
  frame - confirmed by reading MLT's own filter source,
  `~/dev/mlt/src/modules/core/filter_crop.c`, after a single-instance
  `active=1` attempt produced a real render with zero measurable
  difference from an uncropped baseline), and a second bare instance
  with only `active: 1` (which reads those frame properties and
  performs the actual pixel crop - unscaled, top/left anchored).
  Verified working with that two-filter shape on the system's stable
  `melt` (7.32.0: a cyan test frame cropped top+bottom cleanly, exact
  pixel count, no scaling). That two-filter shape alone **reliably
  segfaults** (exit 139, reproduced with vertical-only and
  horizontal-only margins separately) **on the from-source `melt`
  build (7.41.0-dev) this project's own test suite prefers by default**
  for real `qtblend` compositing support (`tests/conftest.py`'s `melt`
  fixture) - a real, practical blocker for verifying this project's own
  output, not a theoretical concern. Root-caused with `gdb -batch -ex
  run -ex "bt full"`: the crash is inside `libswscale.so.7`'s
  `sws_scale()`, called from the AVFORMAT CONSUMER, not from the crop
  filter itself - it's the encoder choking on a post-crop frame that's
  no longer the project's profile size, not a bug in `crop`'s own logic.

  **The fix**: after the two crop filters, add the same `qtblend`
  "refill to canvas" filter every video track already gets by default
  (rect covering the full frame) - this forces the frame back to full
  profile size before the AVFORMAT CONSUMER ever sees it, sidestepping
  the `libswscale` crash entirely while also being the natural way to
  express crop's "then scale back up to fill" behavior a user actually
  wants (an unscaled crop alone would leave black bars). Verified
  against the generator's own shipped output (not just the standalone
  probe that found the workaround): a checkerboard test pattern cropped
  60px off the top of a 240px canvas and rescaled back to fill it shows
  its vertical period stretched by the predicted 240/180 = 1.333x
  factor, measured as fewer black/white transitions along a vertical
  scanline than an uncropped baseline
  (`tests/test_melt_validation.py::test_crop_produces_a_real_crop_and_fill`).
  New `crop:` field on `timeline.tracks[].clips[]` (a
  `{top, right, bottom, left}` dict, pixels, mutually exclusive with
  `position:`/`size:`/`position_keyframes:` since the refill pass
  always targets the full canvas). The full reasoning - both for the
  two-instance mechanism and for the refill-based workaround - lives as
  a code comment on `xmlgen.py`'s `_add_crop_filters`, per the "explain
  why this and not the 'normal' method" bar. Recognized on import too:
  `reverse.py`'s `_match_crop_filters` looks for the exact
  passive-then-active filter pair this generator itself writes and
  reconstructs the same `{top, right, bottom, left}` dict
  (`tests/test_reverse.py::test_round_trip_preserves_crop`).
- **Animated/keyframed picture-in-picture position** — quick win,
  **implemented**. `ClipPlacementSpec.position`/`size` and the
  `qtblend` `rect` property were already plumbed, but only as one
  static rectangle per clip (`0=...`, no second keyframe) - new
  `position_keyframes:` field on `timeline.tracks[].clips[]` (a
  `[{at, position: [x,y], size: [w,h]}, ...]` list, pixels, mutually
  exclusive with the static `position:`/`size:` pair) extends the
  *exact same* per-track `qtblend` filter every video track already
  gets to a multi-keyframe `rect` string, no new MLT service needed.
  Verified by real render before wiring in (a bare-metal probe, not
  assumed from Ken Burns' precedent - Ken Burns keyframes a *different*
  property, `affine`'s `transition.rect`, not `qtblend`'s plain
  `rect`): a small PiP box moved cleanly from one corner to the other
  over a clip's duration with a genuine multi-keyframe `rect`
  (`tests/test_melt_validation.py::test_position_keyframes_produces_a_real_animated_pip`).
  Recognized on import too - unlike `ken_burns_from:`/`ken_burns_to:`
  (an exact two-keyframe shape this generator itself constructs),
  any valid multi-keyframe `qtblend` rect is a free, plain user-facing
  value and round-trips directly (same bar the *static* `position:`/
  `size:` case already held to) - this actually *closes* a
  pre-existing gap: `reverse.py` used to flag *any* multi-keyframe
  qtblend rect as unsupported (`reverse.qtblend_animated_position`),
  since nothing this generator produced before v0.7 could have written
  one.
- **`source_in:`/`source_out:`** — added mid-cycle, outside the
  original v0.7 scoping, in response to a direct question about
  whether a video clip's speed-adjusted trim window could be expressed
  in real source seconds instead of the already-warped timeline `in:`/
  `out:` use. New fields on `timeline.tracks[].clips[]`: unlike `in:`/
  `out:` (relative to the *warped* timeline once `speed:` is set,
  matching kdenlive itself), `source_in:`/`source_out:` always mean
  real seconds into the source file regardless of `speed:` - dividing
  by `speed` internally (`in_s = source_in / speed`) so the caller
  doesn't do that math by hand. Both-or-neither; mutually exclusive
  with `in:`/`out:`/`duration:` and with `speed_ramp:` (already
  mutually exclusive by construction, since `speed_ramp` requires an
  explicit `out:`/`duration:` and `source_in:`/`source_out:` reject
  being combined with one). Verified by real render through the
  generator's own output: `source_in: 1.0, source_out: 3.0, speed: 2.0`
  on a 4s red/green/blue/yellow test source correctly extracted just
  the green+blue middle section and played it back at 2x, landing on a
  1s output starting green and ending blue
  (`tests/test_melt_validation.py::test_source_in_out_combines_correctly_with_speed`).

  **Verifying this surfaced a real, separate bug**, unrelated to
  `source_in:`/`source_out:` itself (which verified cleanly at 2x): a
  sub-1.0 (slow-motion) `speed:` on video plays the source through at
  roughly 1:1 real time and then *freezes on the last frame* for the
  remainder of the correctly-computed stretched duration, instead of
  actually stretching it - but **only on the from-source dev `melt`
  build (7.41.0-dev)** this project's test suite prefers; the exact
  same generated `.kdenlive` plays the correct, smoothly-stretched
  progression on the system's stable `melt` (7.32.0). Not previously
  caught because every existing `speed:` test only ever checked
  rendered *duration*, never *content*, for the sub-1.0 video case -
  see BACKLOG.md.
**`Freeze-frame / hold` was scoped alongside these two but not reached
this cycle** - moved back to BACKLOG.md rather than held open (MLT's
`freeze` filter and `hold` producer are confirmed present via `melt
-query`, but no real render has confirmed the frozen frame actually
holds; picked back up as a v0.8 quick win, below).

**Explicitly deferred, not in v0.7**: `mask_apply`/`mask_start` family
(leads/sketches only, unverified); subtitles, proxy generation, generic
color grading (`lift_gamma_gain`), richer title clips (large standalone
lifts); video stabilization, motion tracking, LUT-based grading
(`avfilter.lut3d`), audio ducking (reserved for a later
"kdenlive-native/competitor-parity effects" theme).

## v0.8 — shipped (2026-08-17)

Headline: an "old film" look family - `oldfilm` (Y-jitter + brightness
flicker + patchy "uneven develop", the strongest match for the
original "random black burned patches" question that kicked off this
round's research), `dust` (scattered specks), `lines` (scratch lines),
and `grain` (film grain). All four are kdenlive-native filters
(Marco Gittler, GPL, not frei0r) - confirmed present, with parameters
matching exactly, on both the from-source dev `melt` build and the
system's stable one (no `crop`-style build-specific issue) via `melt
-query filter=<name>`. Their `requires_in_out="1"`/`unique="1"` XML
attributes turned out to be UI-only concerns (kdenlive's own
`assetparametermodel.cpp` uses `requires_in_out` purely to decide
whether to show an in/out zone-trim widget in the GUI; `unique` has no
engine-side reference anywhere in kdenlive's C++ source beyond the XML
attribute itself) - confirmed empirically too: rendered identically
with or without an explicit `in`/`out` on the `<filter>`, and two
stacked instances of the same filter didn't crash or misbehave.

**Implemented** as four independent, freely combinable boolean toggles
on `timeline.tracks[].clips[]` (video), each applying kdenlive's own
filter defaults - no per-parameter overrides in this first cut,
deliberately narrow scope (same call `overlay_text`'s fixed layout
made):

- `old_film: true` -> `oldfilm` (`delta`/`every` Y-jitter,
  `brightnessdelta_up`/`brightnessdelta_down`/`brightnessdelta_every`
  flicker, `unevendevelop_up`/`unevendevelop_down`/
  `unevendevelop_duration` the "burned" look itself).
- `film_dust: true` -> `dust` (`maxdiameter`/`maxcount`).
- `film_scratches: true` -> `lines` (`line_width`/`num`/`darker`/
  `lighter`) - named "scratches" in the schema since bare "lines" is a
  confusing, over-generic word for a user-facing field.
- `film_grain: true` -> `grain` (`noise`/`contrast`/`brightness`).

Verified against the generator's own shipped output, not just the
standalone probe: a flat solid-gray frame has pixel-value stddev of
exactly 0.0 unfiltered, and a stable ~15.0-15.3 with all four filters
enabled, checked at five different timestamps across the clip - a
real, camera-independent effect, not a lucky single-frame sample
(`tests/test_melt_validation.py::test_old_film_produces_a_real_render_difference`).
Recognized on import by an exact match on every parameter (same bar
Ken Burns/reveal filters hold to, since there's no per-parameter
override yet to represent a hand-customized value) - anything else
falls to `x_kdenlive_raw`
(`tests/test_reverse.py::test_round_trip_preserves_old_film_flags`).

Two quick wins alongside it, both implemented:

- **Freeze-frame / hold** - carried over from v0.7. MLT's own `freeze`
  filter (kdenlive-native, LGPLv2.1 - matches kdenlive's own "Freeze"
  effect exactly, `~/dev/kdenlive/data/effects/freeze.xml`). Verified
  by real render before writing any generator code, against a
  red/green two-segment probe: with `freeze_before`/`freeze_after`
  both left at their default 0, the whole clip holds on frame N;
  `freeze_after=1` plays normally up to N and then holds; `freeze_before=1`
  is the mirror image - cross-checked against MLT's own filter source,
  `~/dev/mlt/src/modules/kdenlive/filter_freeze.c`'s `do_freeze`
  condition, not just the XML effect definition. No build-specific
  issue like `crop` hit. New `freeze_at:` (seconds, relative to the
  clip's own post-trim timeline) plus `freeze_before:`/`freeze_after:`
  fields on `timeline.tracks[].clips[]`. `frame` is a plain
  user-facing value (same "any value round-trips freely" bar
  `chroma_key`/`focus_rect` hold to), so it's recognized on import
  directly, no exact-match restriction needed
  (`tests/test_reverse.py::test_round_trip_preserves_freeze_at`).
- **`heart`/`bowtie` luma shapes for `wipe`** - two new procedural
  `wipe` transitions, extending the same non-copied, procedurally
  generated 64x64 `.pgm` luma set `wipe` already uses (ten shapes
  before this). `heart` uses the sketched implicit curve
  `(x²+y²-1)³ - x²y³`, with its raw magnitude cube-root-compressed and
  linearly stretched to the full 0-255 range (the raw cubic term only
  spanned ~88-227 uncompressed, narrower than every other luma in the
  set). `bowtie` uses the sketched `255·(|x|-|y|+0.5)` directly,
  clamped - but its actual reveal order turned out to be top-center/
  bottom-center first, left/right mid-edges last (a horizontal-band
  reveal), not the "vertical center band" the original backlog sketch
  described in prose; the formula was right, the plain-English
  description of what it'd look like wasn't. Verified with a real
  `melt` render (not just a rendered-PNG visual check first): sampling
  early-reveal vs. late-reveal points mid-transition confirmed both
  are genuine spatial wipes, not instant cuts
  (`tests/test_melt_validation.py::test_scenes_wipe_heart_and_bowtie_produce_real_spatial_wipes`).

## v0.9 — shipped (2026-08-17)

Headline: professional color grading via MLT's own `lift_gamma_gain`
filter (Meltytech, LGPLv2.1 - "lift=shadows, gamma=midtones,
gain=highlights", the classic three-way grading formula every serious
NLE exposes). Confirmed present via `melt -query filter=lift_gamma_gain`:
nine independent float parameters, `lift_r`/`lift_g`/`lift_b` (default
0.0), `gamma_r`/`gamma_g`/`gamma_b` (default 1.0), `gain_r`/`gain_g`/
`gain_b` (default 1.0) - a neutral/no-op filter at its defaults, single
pass, no external file, no new mechanism (same "one filter, plain
numeric parameters" shape `focus_rect:`/`chroma_key:` already
established), and - unlike the quick win below - a native MLT filter,
not avfilter-bridged, so none of the colorspace risk that's bitten
`chroma_key`/`glitch` before.

**Implemented.** New `color_grade:` field on `timeline.tracks[].clips[]`:
a dict with up to three optional `[r, g, b]` triples -
`{lift: [r,g,b], gamma: [r,g,b], gain: [r,g,b]}` - any subset, each
omitted group falls back to MLT's own neutral default for that group;
an empty dict, wrong-shaped triples, or negative values are all
rejected at validation time rather than silently passed through to
melt. Verified by real render against the generator's own output: a
neutral gray source with `gain: [2.0, 1.0, 1.0]` measured a red
channel boosted from ~128 to past 158 (>30 delta), green/blue
essentially unchanged - a real, targeted, per-channel effect, not a
global brightness bump
(`tests/test_melt_validation.py::test_color_grade_produces_a_real_render_shift`).
Recognized on import: values are plain user-facing numbers (any value
round-trips, same bar `chroma_key`/`focus_rect` hold to), reconstructing
only the groups that differ from MLT's own neutral default.

Chosen over `deshake` (video stabilization) and audio ducking
(`avfilter.acompressor`) after a closer look: `deshake` still needed a
real-render verification pass before committing to headline status,
and true ducking (one track's level reacting to another's) would need
a cross-track relationship this schema doesn't have yet - closer to
new design work than a "quick win," so neither was ready to commit to
this cycle without more groundwork. Both stay in BACKLOG.md.

**Quick win — investigated and dropped**: LUT-based grading
(`avfilter.lut3d`, FFmpeg/libavfilter) - applying a `.cube` 3D LUT file
to a clip, the DaVinci-standard interchange format. Confirmed present
via `melt -query filter=avfilter.lut3d`, but hit the exact same
"Cannot set src colorspace" bug `avfilter.chromakey`/`avfilter.
rgbashift` already ran into (see v0.5's `chroma_key`/`glitch` entries)
on this project's preferred from-source dev melt build - confirmed by real
render, not just the warning: an identity `.cube` LUT and an inverting
one both produced pixel-identical output to an unfiltered render.
Cross-checked against the system's stable melt (7.32.0), where it
works correctly (the inverting LUT genuinely flipped red to cyan) - so
this is build-specific like `crop`'s original segfault, but **unlike
`crop`, no workaround was found**: the only native (non-avfilter)
alternative, `rgblut`, is a per-channel 1D lookup table, not a real 3D
cube LUT, so it can't stand in for real `.cube` file support the way
`frei0r.bluescreen0r` stood in for `chroma_key`. Not shipped - no
`lut:` field exists.

**Also scoped, research-first** (same discipline `pillar_echo`/`dance`/
`freeze-frame` got): MLT's `mask_apply`/`mask_start` pair (Meltytech,
LGPLv2.1) - real masking/rotoscoping, a "snapshot, then composite via
a transition" bracket around one or more masking filters. **Mechanism
confirmed real and safe on both melt builds** (no `crop`-style crash),
via a standalone probe (gray source -> `mask_start`(`frei0r.alphaspot`)
-> `lift_gamma_gain` -> `mask_apply`(`frei0r.composition`), pixel-
sampled center vs. corner): kdenlive's own "Alpha shapes (Mask)" effect
is exactly this pairing with `frei0r.alphaspot` as the shape - the same
filter this project already drives for `reveal: iris`
(`_add_reveal_style_filter`/`_match_reveal_filter`). **But the schema
design doesn't converge yet**: the alphaspot shape marks the
*protected* zone, not the "spotlight" - the encapsulated filter's
effect shows *outside* the shape, the inverse of the naive mental
model (tried flipping via `Min`/`Max`, no effect - not a simple range
flip). More fundamentally, MLT's actual capability is generic ("bracket
*any* filter(s), masked by *any* alpha-writing filter"), and this
schema has no existing precedent for "wrap an arbitrary sub-filter in a
masked region" - every filter field today is a flat, single-purpose
property. Committing to one narrow slice (e.g. hardcoding
`lift_gamma_gain` as the only maskable filter) would preempt a real
design decision rather than resolve one. Moved to BACKLOG.md with the
full finding intact, for its own dedicated scoping round rather than
folded into this cycle.

## v0.10 — shipped (2026-08-17)

Headline: video stabilization via MLT's own `deshake` filter (Vid.Stab,
GPLv2, Georg Martius/Jakub Ksiezniak - the single-pass version of
`vidstab`, same algorithm, no intermediate `.trf` results file, no
two-pass build workflow this project doesn't have). Confirmed present
with identical parameters on both melt builds - no `crop`/`lut3d`-style
build-specific issue. Chosen over `vidstab` (the full two-pass
stabilizer) specifically because it fits the "one filter, static XML"
shape every filter shipped so far has had.

**Implemented.** New `stabilize:` field on `timeline.tracks[].clips[]`:
a plain boolean toggle at MLT's own defaults for all eleven parameters
(`shakiness`/`accuracy`/`stepsize`/`mincontrast`/`smoothing`/
`maxshift`/`maxangle`/`crop`/`zoom`/`optzoom`/`zoomspeed`) - no
per-parameter overrides in this first cut, same narrow-first-cut call
`old_film:`'s toggle-only v0.8 scope made. Verified with a real,
quantitative motion measurement, not just a visual check: a synthetic
"shaky handheld camera" source (a static high-contrast square viewed
through an animated-crop window) rendered through the generator's own
output, tracking a fixed feature's frame-to-frame centroid movement -
**mean movement dropped from 18.9px (unstabilized) to 3.7px
(`stabilize: true`)**, identical results on both the from-source dev
build and the system's stable one
(`tests/test_melt_validation.py::test_stabilize_reduces_frame_to_frame_jitter`).
Recognized on import: any `deshake` filter present means
`stabilize: true`
(`tests/test_reverse.py::test_round_trip_preserves_stabilize`).

Two quick wins alongside it:

- **`mask_apply`/`mask_start`, second design round — investigated
  further, still not implementation-ready, but the real blocker is now
  pinpointed.** v0.9 found the alphaspot shape *protects* the marked
  zone rather than spotlighting it; v0.10 resolved that (`Operation`,
  `frei0r.alphaspot`'s `filter.9`, controls the polarity - `0` protects
  the shape, `1` inverts it) and confirmed the bracket composes cleanly
  with a real shipped effect (`color_grade:`). But a deeper problem
  surfaced: `mask_start` forwards `filter.*` properties to its internal
  `frei0r.alphaspot` instance, and passing **any** Position X/Y value
  through that path collapses the shape to a fixed small patch in the
  top-left corner, ignoring the requested value - a real MLT property-
  forwarding bug (`~/dev/mlt/src/modules/core/filter_mask_start.c`),
  isolated with a dedicated test, not a schema design question anymore.
  No `mask_rect:` field shipped - without reliable position/size
  control there's nothing honest to offer. Full finding, including the
  leading unconfirmed theory (animated-type params may need `"0=value"`
  keyframe format when forwarded via `mlt_properties_pass`) and the
  concrete next step for a third round, moved to BACKLOG.md.
- **Richer title clips, scoped to multi-line text** — **implemented**,
  and turned out simpler than expected: `titles.py`'s default rendering
  path (`kdenlivetitle_wrapper.cpp`'s `PlainTextItem::updateText`) was
  **already splitting on `\n` and laying out real multi-line text
  natively** via Qt's own `QFontMetrics` - a literal newline in title
  text reached MLT's renderer unmodified even before this cycle. The
  only actual bug was `titles.py`'s own text-box size estimate treating
  the whole string as one line, producing a too-wide box and a
  vertically off-center block for 2+ lines. Fixed by sizing width from
  the *longest* line and height as `font_size * (1 + len(lines))`
  (reduces to the original single-line formula at one line). Verified
  by real render: a two-line title shows two distinct bright bands with
  a measurably darker gap between them, not a merged blob
  (`tests/test_melt_validation.py::test_melt_renders_multiline_title_as_separate_lines`).
  `\n` survives a full build→import round trip unchanged - no reverse.py
  changes needed, it already read the raw text node directly
  (`tests/test_reverse.py::test_round_trip_preserves_multiline_title_text`).
  True multi-item titles (independently positioned/styled text boxes)
  stay out of scope, as planned - still a real schema redesign, still
  in BACKLOG.md.

## v0.11 — in progress

Headline: a video's material no longer has to live on one machine. Bin
clips name where each file comes from - an item of the project's shared
Google Photos album (`source:`), a link (`url:`) or another clip turned
into it (`derive:`) - and `video-as-code fetch` gets everything, so the
whole video can be built from a fresh checkout anywhere.

- **`url:` clips** — **implemented** (2026-10-13): for what isn't in the
  album (typically the music), one or more links tried in order until
  one works (`links.py`). YouTube links give the audio only, through the
  external `yt-dlp`, preferring YouTube's own m4a stream as served - the
  opus/webm stream was measured ~36 ms off, enough to put cuts timed to
  the music off; the m4a one matched a previously downloaded copy at
  0 ms over a whole song. Google Drive links (shared "anyone with the
  link") and plain http(s) give the file as is, which is what makes a
  Drive copy a stable fallback for when `yt-dlp` breaks (it does, each
  time YouTube changes; `VIDEO_AS_CODE_YT_DLP` points at a newer one).
  Stored as `<id><ext>`; the lock's new `links:` section records which
  link each came from, its size and sha256
  (`tests/test_links.py`, `tests/test_fetch.py`).
- **`fetch` asks what to do about what it couldn't get** — **implemented**
  (2026-10-13). First, a failure no longer stops the rest: an album name
  that isn't there, a download or album listing that broke, a clip none
  of whose links work - each becomes a `Problem`, and everything else is
  still fetched. Then, in a terminal, each problem is put to the user:
  retry, give a link instead, name the album item it really is, use a
  local file (this machine only, recorded as `local: true` in the lock)
  or skip. A link or a new name is written straight into the project
  YAML as a text edit that keeps comments and layout (never parse +
  re-dump), checked by parsing the result - an edit that doesn't come
  out as intended leaves the file untouched - and `fetch` runs again for
  what's still missing. Outside a terminal it fails as before, listing
  everything (`fetch_resolve.py`, `tests/test_fetch_resolve.py`).
- **`derive:` upscaling with Real-ESRGAN** — **implemented** (2026-10-13): an
  `upscale: 4` operation, so a low-resolution album item (Google Photos'
  own animations are ~800x600) is enlarged when generated instead of
  kept as a hand-upscaled file (`upscale.py`, `tests/test_upscale.py`,
  with a stand-in for the real program). Checked for real on an 850x640,
  3-frame gif: 3400x2560 out, delays kept exactly, 10.5 min on an
  integrated GPU - and smoother than the earlier hand-made upscale, whose
  skin tones had visible colour banding. `realesrgan-ncnn-vulkan` (realesrgan-x4plus
  model) runs on any Vulkan device, an integrated GPU included - slowly
  there (a minute or two per frame), which is fine for stills and short
  gifs, so those are what it covers; repeated gif frames are upscaled
  once, and the gif is reassembled with its original per-frame delays.
  `fetch` offers to download the program (a pinned release, checked
  against its sha256) when a project needs it and it isn't installed.
- **One local file per album item** — **implemented** (2026-10-14): a
  `derive: {source: X}` input resolves to the file of a plain `source:`
  clip of the same item when there is one (`derive.album_input_path`),
  instead of being downloaded again under X's own name. Found on a real
  project where a clip and its louder copy kept the same original twice,
  and where a preview turning every video into a light derive: copy
  would have downloaded ~90 videos a second time.
- **`fetch --prune`** — **implemented** (2026-10-14): deletes album items
  used only as derive: inputs once everything made from them is
  generated. The output's recorded input sha256 matching the lock's is
  enough for validate/build/render to keep using it
  (`derive.is_current_by_record`), and `fetch` doesn't download the
  input again unless a derive: made from it changes. Roughly 2.5 GB on
  that same project, mostly long originals of which seconds are used.
- **`render --split`** — **implemented** (2026-10-14, `splitrender.py`,
  `tests/test_splitrender.py`): for machines without the RAM for one melt
  run. The picture in segments, each from a copy trimmed to its clips,
  joined without re-encoding; the sound in one pass, muxed in. Segments
  are aligned power-of-two blocks (10 to 1280 frames) halved until a
  memory estimate fits `--max-mem` (default 60% of what's available), so
  an edit only re-plans the segments around it, and each segment's
  render is cached by its trimmed project and input files. Measured on a
  real project (320x180, 156 tracks, a loaded 4-core machine): a melt
  start costs ~5 s and rendering ~0.37 s per frame, so its 758 fixed
  10-frame segments spent more time starting melt than rendering; a
  1.2 GB budget plans 32 segments instead. The estimate (baseline +
  ~20 B per pixel of each still played, ~45 B per pixel of each video,
  plus every track's frame at the profile size) came out 1.4-2x the peak
  RSS actually measured - on the safe side. `VIDEO_AS_CODE_MELT` points
  at a melt other than PATH's (e.g. one built from source).
- **`--proxy WxH` on `build`/`render`** — **implemented** (2026-10-14):
  previews from light H.264 copies of only the source ranges the
  timeline uses (`proxies.py`), cached in `.proxies/`, with placements'
  source times shifted (flat `speed:` to the nearest warped frame,
  `speed_ramp:` by its source offset) and `crop:` margins scaled to the
  copy. Two things measured on the way, both against real renders:
  `crop:` margins are in the file's own pixels for video producers too
  (not only images), and MLT ignores colour tags it doesn't need -
  guessing an untagged video's matrix by height (BT.709 from 720 lines,
  BT.601 below) - so derive now converts an untagged HD video's colours
  when scaling it below 720 lines, while a tagged one (phones tag
  theirs) keeps its tags and plays the same at any size. On a real
  project's phone videos a proxied render matched the full one within
  compression noise, and a cached proxied render took ~8 s where the full
  one took ~23 s. As a bonus, every copy starts near what's used, so
  MLT's timewarp freeze with a large `source_in` can't happen in a
  proxied render. Open: rotated videos (rotation metadata) haven't been
  tried - none of the real project's had any.
- **Automatic pruning before each render** — **implemented**
  (2026-10-14, `autoprune.py`, `tests/test_autoprune.py`):
  `project.sources.prune_after_days` (default 7, 0 = never; `render
  --no-prune` skips it once). Before rendering, deletes only what the
  tool can bring back by itself and the render doesn't read: album items
  used only as derive: inputs whose outputs stand on their own, album
  items nothing uses any more, and stale files in `.derived/` and
  `.proxies/` - each once no build/validate/render has used it for that
  many days (recorded per file in `.fetch_cache/last_used.json`, shared
  by every YAML in the folder, so a project and its preview keep each
  other's files alive). A file seen for the first time only starts
  counting; one the lock doesn't know is never touched.
- **frei0r with the distribution's melt** — **fixed** (2026-10-22): Ubuntu's
  own melt 7.36 saw no frei0r plugin at all - they're in
  /usr/lib/<triplet>/frei0r-1, where MLT doesn't look - so iris, flip,
  glitch, delaygrab, cairogradient and chroma_key rendered as nothing
  with it, silently (the tests didn't notice: they prefer a melt built
  from source, given FREI0R_PATH). `probe.melt_env` now sets FREI0R_PATH
  for every render, unless it's already set (`probe.frei0r_dirs`,
  `tests/test_mltdata.py`).
- **OpenShot's transitions as wipe shapes** — **implemented** (2026-10-22):
  `luma: openshot/<name>` takes any of the 412 masks openshot-qt ships
  (`lumas.py`), read from the user's install - they're GPL-3.0+, so not
  bundled - and converted to PGM once into the user cache: read as
  JPG/SVG, MLT keeps the map's own proportions and the wipe comes out
  off-centre. Chosen over running libopenshot itself, since OpenShot's
  transitions are exactly that - a mask its Mask effect plays as MLT's
  `luma` does - so they render in the same melt pass and open in
  kdenlive. Also `luma_softness`/`luma_reverse` (`softness`/`reverse`
  on `transitions:`), the latter emitted as luma's `invert`: its own
  `reverse` property, found by rendering, changes nothing. `video-as-code
  transitions` lists them (`tests/test_lumas.py`,
  `tests/test_melt_validation.py::test_openshot_luma_wipes_the_whole_frame`).
- **`video-as-code gallery`** — **implemented** (2026-10-22): a page,
  opened in the browser, with every transition and effect the YAML
  exposes, each with its snippet and a preview rendered by `render`
  itself from a tiny project (`gallery.py`'s catalog), cached in the
  user cache folder by project, samples, the package's code and melt;
  the wipe shapes are played by the page itself from their maps. The
  catalog is also checked to stay valid (`tests/test_gallery.py`).
- **`melt:` and `melt/<service>`** — **implemented** (2026-10-22): any
  filter (a clip's `melt:` list) or transition (`transition:
  melt/<service>` on a scene, `type: melt/<service>` on `transitions:`)
  of the installed melt, by service name, with its properties written
  as given (`meltpass.py`). Checked against `melt -query`: an unknown
  service or property is an error with the closest names (frei0r's
  parameters by number or by name, both of which MLT takes), a service
  melt doesn't describe takes any; with no melt, nothing is checked.
  `import` now brings back filters over a whole clip and two-track
  transitions it doesn't otherwise recognize as these entries, when the
  installed melt has them and takes their properties, instead of
  `x_kdenlive_raw` (`tests/test_meltpass.py`,
  `tests/test_melt_validation.py::test_melt_filter_and_transition_render`).
- **`transition: gl`** — **implemented** (2026-10-22): gl-transitions'
  125 GLSL transitions (npm 1.76.1, GLSL ES 3.00 form; MIT, two BSD),
  bundled with their parameters' types, defaults and ranges
  (`gltransitions.py`). MLT runs no GLSL of its own, so a frei0r
  "mixer2" plugin of this project's (`gltransition.c`, EGL + GLES 3, no
  window; Mesa's surfaceless platform, else an EGL device, else the
  default display) renders them in melt's pass, built by `video-as-code
  install-gl-transitions` and put first on FREI0R_PATH by
  `probe.melt_env`. Chosen over pre-rendering each overlap (seams, a
  re-encode, not a transition in kdenlive) and over an MLT module
  (tied to MLT's version). Found along the way: MLT reads a frei0r
  string parameter as an animation, so the uniforms are passed as
  `name:v,v name:v` - as `name=v;...` they were keyframes and never
  arrived (`tests/test_gltransitions.py` renders a vec3 one); and a
  render with 4 threads (`real_time=-4`) came out identical to one
  with 1.
- **`lut:`** — **implemented** (2026-10-22): a 3D LUT file (`.cube`,
  `.3dl`, `.dat`, `.m3d`) on a clip, relative to the project file, with
  `lut_interp`; emitted as kdenlive's own "Apply LUT" (`avfilter.lut3d`,
  `av.file`/`av.interp`) and recognized by import. Dropped in v0.9 (it
  did nothing on the from-source melt); checked again by real render
  with an inverting LUT, it now works on both the system's melt (7.36)
  and the from-source one (7.41), and an identity LUT leaves the
  picture as it was (`tests/test_lut.py`,
  `tests/test_melt_validation.py::test_lut_applies_the_lut`).
- **Presets: `presets:`, `use:`, `presets_file:`** — **implemented**
  (2026-10-22): named sets of keys, used by name anywhere a project file
  has a map (`presets.py`). Expanded right after the file is read, so
  they work with every key; keys written next to `use:` win, a value
  replacing the preset's whole; presets may use others (a circle is an
  error); a shared file's presets come first, the project's own replace
  them. Relative paths in a preset are the using file's
  (`tests/test_presets.py`).
- **`parts:` - a project in several files** — **implemented** (2026-10-22):
  a main file lists part files, which may list parts; each becomes a
  kdenlive nested sequence (modeled on kdenlive's own
  tests/dataset/test-nesting.kdenlive: a sequence's tractor placed in
  another's track by its uuid), and a file with parts one that plays
  them in turn on two alternating tracks, overlapping by the incoming
  part's transition (`parts.py`; xmlgen.py builds each sequence with
  `_build_sequence`). Parts resolve their own paths, keep their own bin
  ids (namespaced), inherit presets; cycles are errors; `--part` takes
  one alone. Checked by real render: a dissolve between two parts
  mixes them halfway (`tests/test_parts.py`). Not yet: fetch, --proxy,
  --split, autoprune and import with parts (BACKLOG.md).
- **`parts:` with the rest of the tool** — **implemented** (2026-10-22):
  `fetch` goes through each part in its own folder (parts.register makes
  load_yaml read a part file as the project it is part of, so fetch's
  interactive fixes land in the part's file), autoprune too (not with
  `--part`), `--proxy` makes each part's copies in its folder, `--split`
  places a part's clips where the part plays (splitrender._placements),
  and `import` turns a timeline of whole sequences in turn back into
  `parts:` and a file per part (`tests/test_parts.py`).
- **Lottie animations** — **implemented** (2026-10-22): a `.json` or
  `.tgs` bin clip is `type: animation`, played by melt's glaxnimate
  producer (kdenlive's ClipType 16), its length read from its own
  frames. Checked by real render over a clip below; a melt without the
  module (a from-source build may lack it) is a validation error
  (`tests/test_animation.py`).
- **`glsl:` - GLSL effects on a clip** — **implemented** (2026-10-22):
  a frei0r filter plugin, `frei0r.glfilter` (`glfilter.c`, the
  single-clip sibling of gltransition.c, built with it), runs a GLSL
  ES 3.00 `vec4 effect(vec2 uv)` over a clip's picture with `time` and
  `progress`; six shaders of our own bundled (crt, chromatic, wave,
  glitch, kaleidoscope, vhs - each checked to compile and draw by a real
  render) and the user's own `.glsl` files (`glfilters.py`,
  `tests/test_gltransitions.py`).
- **libopenshot's effects: `derive: {openshot: [...]}`** — **implemented**
  (2026-10-22): its 29 effects (video and audio) applied, in order, to a
  clip when it's generated, after derive:'s ffmpeg operations
  (`openshot_effects.py`). libopenshot's bindings are for the system's
  Python and its Qt 5 can't share a process with MLT's Qt 6, so a script
  of its own (`openshot_render.py`) runs with that Python - also to
  describe its catalog, which validates each effect and property (kept
  in the user cache per libopenshot version). Checked by real render:
  Negate on a video and on a still (`tests/test_openshot_effects.py`).
  `Caption` and `Mask` refused, saying why (the first drew nothing even
  inside a libopenshot timeline of its own; the second needs a reader
  for its mask). The gallery has a section with each effect, at its
  defaults, over the video sample or the sample sound (ChromaKey over
  the green screen, Deinterlace over the combed video); the two refused
  ones grey, with the reason.
- **`video-as-code docs`** — **implemented** (2026-10-22): the README
  and `docs/*.md` as a local web (Python-Markdown, the `docs` extra),
  built under the user cache next to the gallery, which is part of it:
  the reference's key rows get anchors (`key-<name>`), each card in the
  gallery names its key and links to its row (`gallery.doc_key`: the
  most specific key of its snippet the reference has), and each row
  with cards links to the first one (written by the gallery to
  `doc-cards.json`); a link to a card opens its tab and section and
  shows it even when the "hide" box would hide it. Built on the user's
  machine, not published (their melt, their wallpapers, OpenShot's
  GPL maps). `docs.py`, `tests/test_docs.py`.
- **`video-as-code docs --public <folder>`** — **implemented**
  (2026-10-22): a copy of the docs to publish (GitHub Pages from a
  `gh-pages` worktree; its `.git` left alone), built apart in the cache
  (`public/`): the gallery over generated pictures, without OpenShot's
  GPL wipe shapes or the user's own LUTs, `--with` refused; only the
  pages and the previews shown copied, with an `index.html` to the docs,
  a `.nojekyll`, and on each page when and with which melt it was built.
  Replaces only a folder it wrote itself.
- **`build --format openshot`** — **implemented** (2026-10-24, `osp.py`,
  `tests/test_osp.py`): the timeline as an OpenShot project - files,
  tracks as layers, trims, fades as opacity/volume, dissolves, wipes as
  OpenShot's Mask transition (direction, `reverse` and `softness`
  matched), guides as markers; a derive: of `openshot:` effects alone as
  native, editable effects. What has no counterpart is left out, each
  thing named. Written by libopenshot itself (openshot_render.py
  `--project`) and checked by playing it with libopenshot's Timeline
  against melt's render: same wipe direction both ways, the dissolve's
  half-way mix, fades on their frames, the Wave effect within encoding
  noise of the generated file.
