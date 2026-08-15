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
- **Cross-track transitions reconstructable into `scenes:`**: a wipe's
  `luma` transition between two content tracks is now flagged instead of
  silently dropped on import, but there's still no way to rebuild it back
  into a `scenes:`-level `transition:` — the low-level model has no
  cross-track-transition concept at all on the import side.
- **`kdgen reconstruct`: matching through fades**: known limitation,
  documented but not addressed at the root — matching compares against
  each asset's *plain* content, so a segment that's mid-fade (darkened/
  quieted relative to source) can score too poorly to match confidently,
  and segmentation can split a fade into spurious extra segments.
- **`kdgen reconstruct`: background audio continuity across cuts**:
  flagged inline in `reconstruct.py` as real and out of scope —
  background audio commonly doesn't segment cleanly to match video cuts;
  a harder, separate problem this doesn't attempt to solve.

## Capabilities not yet touched at all

Not documented gaps — real kdenlive project capabilities this generator's
schema/model has never addressed, found by reading what's already
plumbed in `xmlgen.py`/`models.py`. Roughly smallest to largest lift.

- **Guides/markers**: `xmlgen.py` hardcodes
  `kdenlive:sequenceproperties.guides` to `"[]"`. The placeholder already
  exists in the emitted XML; this is just wiring a YAML `guides:` list
  into that JSON. Smallest lift of anything here.
- **Animated/keyframed picture-in-picture position**:
  `ClipPlacementSpec.position`/`size` and the `qtblend` `rect` property
  are already plumbed, but only as one static rectangle per clip
  (`0=...`, no second keyframe). Extending to multiple keyframes for
  pan/zoom/move reuses the same filter — no new MLT service, just a
  richer `rect` property string.
- **Crop**: no cropping capability exists anywhere in the schema —
  `rect`/`position`/`size` only scale/position via `qtblend`, they don't
  clip source pixels. Would need a new filter (MLT's `crop` or `affine`),
  same shape as mute (one new filter) but genuinely new vocabulary.
- **Color correction/grading**: nothing touches pixel color beyond the
  `brightness` filter used for fades. A real grading filter (e.g.
  `lift_gamma_gain`) would be a new filter category with no existing
  analog to copy from.
- **Speed ramps (non-flat timewarp)**: `speed:` is a single flat
  multiplier for the whole clip. kdenlive's speed-ramp effect uses a
  keyframed mechanism, not just a fancier `timewarp` producer — new
  mechanism, no existing analog.
- **Subtitles**: completely absent from schema/models/xmlgen; kdenlive
  subtitles are a separate track type/file format with no relationship
  to anything currently emitted. Large lift, new mechanism end-to-end.
- **Proxy clip generation**: `xmlgen.py` explicitly hardcodes
  `enableproxy=0`/`generateproxy=0`. Real support means generating actual
  proxy media files, not just flipping a flag — out of proportion with
  the rest of the schema's "just emit static XML" scope.

## Ergonomics, validation & docs gaps

Not missing MLT capabilities — places where the CLI, schema validation,
or examples don't live up to the bar the project already holds itself to
elsewhere.

- **`kdgen render` discards its intermediate `.kdenlive`**: it writes the
  built project into a `tempfile.TemporaryDirectory()` that's deleted
  right after `melt` runs, with no `--keep-kdenlive`/`-k` flag to keep the
  file it just generated — even though `build` and `render` share the
  same `xmlgen.write` call.

## Track/project metadata not round-tripped

- **Track names are discarded in both directions**: `reverse.py`
  hardcodes reconstructed track names to `V1`/`A1`-style
  auto-generated names, never reading a real name from the source XML.
  On the generate side, `TrackSpec.name` exists and scenes.py already
  uses it internally for wipe-transition track lookups, but `xmlgen.py`
  never writes it out as a `kdenlive:track_name` property — a real
  project's custom track names never round-trip, and can't even be
  produced from YAML today.
- **Hidden/locked track state is invisible to the whole pipeline**: no
  code path in `reverse.py`, `xmlgen.py`, `schema.py`, or `models.py`
  reads or writes a track's hidden/locked flag (`<track hide="...">` in
  a real kdenlive project) — neither recognized on import nor settable
  on generation.
- **Bin folder organization is hardcoded off**: every clip producer in
  `xmlgen.py` writes `kdenlive:folderid = -1` unconditionally, and
  `main_bin` only ever declares one fixed folder. There's no YAML concept
  of clip folders/grouping anywhere in `models.py`/`schema.py` — bin
  organization can't be produced or round-tripped at all.
- **No project-level notes/author metadata**: `xmlgen.py` writes
  `kdenlive:documentnotesversion` (a format-version marker) but never
  `kdenlive:documentnotes` (actual notes content) or any author field,
  and there's no `notes:`/`author:` YAML equivalent to set one.

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

- **Glitch / RGB-split ("digital glitch") transition**: `frei0r.glitch0r`
  (block shifting + color-channel glitching, params: frequency/block
  height/shift intensity) and `avfilter.rgbashift` (independent R/G/B
  channel offset, i.e. chromatic aberration) are both real filters on
  this machine's MLT build. Applied as a brief animated burst over the
  overlap window instead of a crossfade, this reproduces the "glitch
  cut" transition style common in CapCut/TikTok-style editors — built as
  a filter burst rather than a true two-source transition, closer in
  shape to a dressed-up cut than to `dissolve`/`wipe`.
- **"Broken TV" / analog-roll transition**: `frei0r.nosync0r` (h-sync
  offset, "broken tv" look) — same filter-burst-over-overlap construction
  as the glitch transition above, different visual family.

## Effects other NLEs have, verified buildable (or confirmed absent) via melt

Found by asking what DaVinci/Premiere/CapCut-class editors have that
kdenlive-generator doesn't yet produce. Several turned out to be effects
kdenlive's *own* UI already exposes (their MLT filters are credited to
kdenlive's own lead developer) — not competitor-exclusive after all, but
still real gaps for this project, since none of them are in the schema.
All confirmed against `melt -query filter=`/`producer=` on this machine's
melt 7.32.0, not assumed.

- **Freeze-frame / hold**: MLT ships a `freeze` filter and a `hold`
  producer (`type: producer`, `title: Still`), both already used by
  kdenlive's own "Freeze" effect. Absent from this project's schema.
- **Video stabilization**: `vidstab`/`deshake` filters present — what
  kdenlive's own "Stabilize" effect uses. Absent from this project.
- **Motion tracking**: `opencv.tracker` present, credited to kdenlive's
  own lead developer (`jb@kdenlive.org`) — this *is* kdenlive's tracker
  effect. Absent from this project.
- **Chroma-key / green-screen**: `avfilter.chromakey` confirmed present;
  kdenlive's own UI already has `frei0r.bluescreen0r`. Absent from this
  project.
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
  relevant if "speed ramps" (already listed above) is ever scoped.
