# Backlog

Unordered pool of things we want to do at some point — no priority, no
version assigned. When one of these gets scheduled into an actual release,
it moves to `ROADMAP.md` under that version; it stays here until then.

## Gaps the code already flags

Each of these is a documented limitation, not a speculative feature — the
code itself already marks the boundary.

- **Richer title clips**: `titles.py` deliberately generates one line, one
  style, no animation, no multiple items. `reverse.py` already recognizes
  a title clip's *presence* but falls back to `x_kdenlive_raw` for
  anything richer (multiple items, animation, image/rect items) since
  there's nothing in the YAML model to represent them.
- **`kdenlive-as-code reconstruct`: matching through fades**: known limitation,
  documented but not addressed at the root — matching compares against
  each asset's *plain* content, so a segment that's mid-fade (darkened/
  quieted relative to source) can score too poorly to match confidently,
  and segmentation can split a fade into spurious extra segments.
- **`kdenlive-as-code reconstruct`: background audio continuity across cuts**:
  flagged inline in `reconstruct.py` as real and out of scope —
  background audio commonly doesn't segment cleanly to match video cuts;
  a harder, separate problem this doesn't attempt to solve.
- **Slow-motion (`speed:` between 0 and 1) video content is silently
  wrong on the from-source dev `melt` build this project's own test
  suite prefers**: found while verifying `source_in:`/`source_out:`
  (v0.7) - a real render at `speed: 0.5` plays the source through at
  roughly 1:1 real time and then *freezes on the last frame* for the
  remainder of the (correctly-computed) stretched duration, instead of
  actually stretching it. Confirmed genuinely build-specific, not a
  bug in this project's own code: the exact same generated
  `.kdenlive`, rendered with the system's stable `melt` (7.32.0)
  instead of the from-source dev build (7.41.0-dev,
  `tests/conftest.py`'s `melt` fixture), plays the correct,
  smoothly-stretched red/green/blue/yellow progression. Fast (`speed`
  > 1) speeds are unaffected on either build - only confirmed broken
  for sub-1.0 magnitudes. Not previously caught because every existing
  speed test (`test_melt_respects_video_speed_change` et al.) only
  ever checked rendered *duration*, never *content*, for the video
  case - duration alone doesn't distinguish "stretched correctly" from
  "played once, then frozen for the same total length." Worth
  revisiting once a newer stable MLT release is available (`crop` hit
  a similarly build-specific issue on this same build - see the
  ROADMAP's v0.7 entry - but that one had a working rescale-based
  workaround; no equivalent workaround has been found for this one),
  and possibly worth adding a content-checking test for the existing
  `speed:` feature itself, not just new features that happen to
  exercise it.

## Capabilities not yet touched at all

Not documented gaps — real kdenlive project capabilities this generator's
schema/model has never addressed, found by reading what's already
plumbed in `xmlgen.py`/`models.py`. Roughly smallest to largest lift.

- **Color correction/grading**: nothing touches pixel color beyond the
  `brightness` filter used for fades. A real grading filter (e.g.
  `lift_gamma_gain`) would be a new filter category with no existing
  analog to copy from.
- **Subtitles**: completely absent from schema/models/xmlgen; kdenlive
  subtitles are a separate track type/file format with no relationship
  to anything currently emitted. Large lift, new mechanism end-to-end.
- **Proxy clip generation**: `xmlgen.py` explicitly hardcodes
  `enableproxy=0`/`generateproxy=0`. Real support means generating actual
  proxy media files, not just flipping a flag — out of proportion with
  the rest of the schema's "just emit static XML" scope.

## Creative transitions beyond kdenlive's own set

kdenlive's UI only exposes `dissolve`/`wipe`-style transitions, but MLT
itself (verified against the real `melt 7.32.0` on this machine via
`melt -query transitions`/`-query filters`, not assumed) ships mechanisms
kdenlive doesn't build a UI for — the same situation `wipe` was in before
this project added it (procedurally generated, not a copy of kdenlive's
own GPL luma maps: [[roadmap]] wipe entry). These would be genuinely new
`transition:` kinds in `scenes.py`, same alternating-track-overlap shape
`dissolve`/`wipe` already use, built entirely from LGPL/BSD MLT/frei0r
services already present — no competitor code involved, just MLT
mechanisms competitors' editors don't expose either.

- **New procedural luma shapes for `wipe`**: cross-referenced against
  gl-transitions.com's open (MIT) 125-transition catalog for concepts
  reproducible via `lumas.py`'s existing gradient/angle/distance-math
  approach (same shape as the project's own non-copied luma set) -
  `circleopen`/`CircleCrop` and `cube`/`BookFlip` turned out to already
  be covered by the `iris`/`flip` transitions now scoped into v0.4 (see
  ROADMAP.md, found independently during the same research round); two
  more are still open, sketched but **not yet rendered/verified**:
  - **heart**: per-pixel implicit heart curve
    `(x²+y²-1)³ - x²y³` (x, y centered/normalized to the frame), mapped
    to a 0-255 gradient around the zero-crossing - a heart-silhouette
    reveal growing from/into center.
  - **bowtie**: `luma = clamp(255 * (|x/w| - |y/h| + 0.5), 0, 255)` -
    two opposing triangular reveal wedges meeting at a vertical center
    band.
- **Leads for the next research round**: `~/dev/kdenlive/data/effects/`
  has ~80 more effect definitions not yet surveyed - the `mask_apply`/
  `mask_start_*` family looked worth a follow-up pass (real masking/
  rotoscoping, distinct from crop/`iris`). Compound-clip import
  recognition (a nested `<tractor>` inside a playlist `<entry>`,
  currently falls through to `x_kdenlive_raw` like any other
  unrecognized structure) also hasn't been investigated - unclear
  whether even *recognizing* the shape on import (without fully
  supporting it) is tractable.

## Effects other NLEs have, verified buildable (or confirmed absent) via melt

Found by asking what DaVinci/Premiere/CapCut-class editors have that
kdenlive-as-code doesn't yet produce. Several turned out to be effects
kdenlive's *own* UI already exposes (their MLT filters are credited to
kdenlive's own lead developer) — not competitor-exclusive after all, but
still real gaps for this project, since none of them are in the schema.
All confirmed against `melt -query filter=`/`producer=` on this machine's
melt 7.32.0, not assumed.

- **Video stabilization**: `vidstab`/`deshake` filters present — what
  kdenlive's own "Stabilize" effect uses. Absent from this project.
- **Motion tracking**: `opencv.tracker` present, credited to kdenlive's
  own lead developer (`jb@kdenlive.org`) — this *is* kdenlive's tracker
  effect. Absent from this project.
- **LUT-based color grading**: `avfilter.lut3d`/`avfilter.lut1d`
  confirmed present — DaVinci-style `.cube` LUT application, distinct
  from (and a smaller lift than) the generic `lift_gamma_gain` grading
  filter already listed above under "Capabilities not yet touched at
  all."
- **Audio ducking/compression/gate**: `avfilter.acompressor`/
  `avfilter.agate` confirmed present, plus a `sox.*` family
  (`sox.compand`, `sox.noisered` for noise reduction) — sidechain-style
  ducking (a Premiere/Resolve staple) is buildable from `acompressor`,
  not currently in the schema.
- **Frame-blend speed interpolation confirmed absent**: `avfilter.
  minterpolate` (true optical-flow retiming, what DaVinci/Premiere use
  for smooth slow-motion) is **not present** in this machine's compiled
  filter list — only `avfilter.tblend` (cruder temporal frame blending)
  exists as a fallback. A genuine, verified ceiling: this build of MLT
  can't do real optical-flow retiming, only a blend-based approximation,
  relevant to speed ramps (`timeremap`, scoped into v0.7 - see
  ROADMAP.md) if a future extreme-slow-motion case ever needs smoother
  interpolation than `timeremap`'s own `image_mode: nearest`/`blend`
  choice provides.
