# Backlog

Unordered pool of things we want to do at some point — no priority, no
version assigned. When one of these gets scheduled into an actual release,
it moves to `ROADMAP.md` under that version; it stays here until then.

## Gaps the code already flags

Each of these is a documented limitation, not a speculative feature — the
code itself already marks the boundary.

- **Richer title clips**: multi-line text shipped in v0.10 (see
  ROADMAP.md) - `titles.py` still generates one style, no animation, no
  multiple items. `reverse.py` already recognizes a title clip's
  *presence* but falls back to `x_kdenlive_raw` for anything richer
  (multiple independently-positioned items, animation, image/rect
  items) since there's nothing in the YAML model to represent them -
  would need title clips to become a list of positioned items rather
  than a flat set of fields, a real schema redesign, not a quick win.
- **`video-as-code reconstruct`: matching through fades**: known limitation,
  documented but not addressed at the root — matching compares against
  each asset's *plain* content, so a segment that's mid-fade (darkened/
  quieted relative to source) can score too poorly to match confidently,
  and segmentation can split a fade into spurious extra segments.
- **`video-as-code reconstruct`: background audio continuity across cuts**:
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
- **A `type: video` track placement never carries its own audio to the
  final render, `mute:` or not** - by design (`hide="audio"` is set
  unconditionally on both of a video track's internal sub-tracks in
  `xmlgen.py`, not just when `mute:` is set), matching real kdenlive's
  per-clip AV-split model and already noted in passing in the ROADMAP's
  v0.6 `dance` write-up - but nothing documents it as a *user-facing*
  gotcha outside that one investigation's context. Worth surfacing more
  prominently (e.g. in the README/schema docs near `mute:`/audio tracks)
  since a first-time user placing a clip on a video track and expecting
  its sound to just work (the common case for a plain clip with
  synced audio, not the independent-fades scenario `mute:` was built
  for) will silently get a mixed-down-to-silence result instead - found
  the same way, via `projects/`.

- **Gallery follow-ups**: audio analysis filters (astats, ebur128,
  volumedetect...) in the analysis section rather than the audio one;
  more than three tries for filters whose ranged parameters didn't
  change anything (a second parameter at a time, or both ends of a
  range); the 10 melt services that fail even at their defaults
  (sox asserts, a few hangs) listed with why rather than as plain
  errors.

## Capabilities not yet touched at all

Not documented gaps — real kdenlive project capabilities this generator's
schema/model has never addressed, found by reading what's already
plumbed in `xmlgen.py`/`models.py`. Roughly smallest to largest lift.

- **Subtitles**: completely absent from schema/models/xmlgen; kdenlive
  subtitles are a separate track type/file format with no relationship
  to anything currently emitted. Large lift, new mechanism end-to-end.
- **Proxy clip generation**: `xmlgen.py` explicitly hardcodes
  `enableproxy=0`/`generateproxy=0`. Real support means generating actual
  proxy media files, not just flipping a flag — out of proportion with
  the rest of the schema's "just emit static XML" scope.

## Assisted quality pass

- **`video-as-code enhance`: find the assets that will look poor and offer
  better versions, asking which one to keep**. A pass over the project's
  material (after `fetch`) that flags, per clip, what will show badly in
  the render: a picture enlarged well past its own pixels (the size it's
  placed at vs its crop, e.g. a 799x450 WhatsApp photo shown at 1812x978),
  heavy JPEG blocking or noise, blur. For each flagged asset it makes
  candidate versions - `upscale: 4` (Real-ESRGAN, already a `derive:`
  operation), a plain Lanczos enlargement with unsharp mask and a touch of
  grain, colour denoise before either - and shows them side by side at
  the size the video will show them (a contact sheet with big labels, or
  one window per asset), then asks which to use (or to keep the
  original). The choice is written into the YAML as `derive:` operations
  on the album original, so nothing extra has to be uploaded anywhere and
  the result stays reproducible from the YAML alone. Needs `derive:` keys
  for the non-AI candidates (`sharpen`, `grain`, `denoise`), and a way to
  pick the AI model (Real-ESRGAN general / anime / a face restorer such
  as GFPGAN for faces). Came out of rebuilding the 2014/15 video by hand:
  five low-res shots, three variants each, chosen from a sheet.
  The same pass should spot a camera's **date stamp** burnt into a photo
  (the orange digits in a corner of old compact-camera pictures - text
  detection on the picture), offer it with the stamp removed by an
  inpainting model (LaMa did it well by hand for 2014/15: a mask over the
  digits, 1024 px windows), and ask; kept as a `derive:` operation too
  (`remove_date_stamp`, or a general `inpaint: {mask}`).

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
  ROADMAP.md, found independently during the same research round).
  `heart`/`bowtie` (also found in this round) shipped in v0.8 - see
  ROADMAP.md. Still nothing further queued from this catalog.
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
video-as-code doesn't yet produce. Several turned out to be effects
kdenlive's *own* UI already exposes (their MLT filters are credited to
kdenlive's own lead developer) — not competitor-exclusive after all, but
still real gaps for this project, since none of them are in the schema.
All confirmed against `melt -query filter=`/`producer=` on this machine's
melt 7.32.0, not assumed.

- **Video stabilization**: `deshake` (single-pass) shipped in v0.10 as
  `stabilize:` (see ROADMAP.md). `vidstab` (the full two-pass
  stabilizer - needs an analysis pass that writes a `.trf` results file
  before a second render pass applies it) is still absent - would need
  a build-time mechanism this project doesn't have, every filter
  shipped so far is single-pass static XML. Worth revisiting only if
  that two-pass workflow question gets solved for something bigger than
  this one filter.
- **Motion tracking**: `opencv.tracker` present, credited to kdenlive's
  own lead developer (`jb@kdenlive.org`) — this *is* kdenlive's tracker
  effect. Also two-pass (analysis + apply, same shape as `vidstab`).
  Absent from this project.
- **LUT-based color grading — investigated and dropped (v0.9); shipped
  since as `lut:` (v0.11), once both melts (7.36, 7.41) applied it**:
  `avfilter.lut3d` hits the same "Cannot set src colorspace" bug
  `avfilter.chromakey`/`avfilter.rgbashift` did (see ROADMAP.md's v0.5
  and v0.9 entries) on this project's preferred from-source dev melt
  build - confirmed by real render (identity and inverting `.cube` LUTs
  both produced pixel-identical output to unfiltered), works correctly
  on the system's stable melt. No native substitute exists this time:
  `rgblut` is a per-channel 1D lookup table, not a real 3D cube LUT, so
  it can't stand in the way `frei0r.bluescreen0r` stood in for
  `chroma_key`. Worth revisiting once a newer stable MLT release is
  available (same category as `crop`'s original segfault and the
  sub-1.0 `speed:` bug above). `lift_gamma_gain`-based grading shipped
  in v0.9 instead - see ROADMAP.md.
- **Audio ducking/compression/gate**: `avfilter.acompressor`/
  `avfilter.agate` confirmed present, plus a `sox.*` family
  (`sox.compand`, `sox.noisered` for noise reduction). Plain single-track
  dynamics compression is buildable directly from `acompressor` today,
  but *real* ducking (a Premiere/Resolve staple - music level reacting
  to a voice track's level) needs a cross-track relationship (one
  track's audio driving another's gain) this schema has no precedent
  for yet - closer to new design work than a single-filter quick win,
  which is why it lost to `lift_gamma_gain` as a v0.9 headline
  candidate.
- **`mask_apply`/`mask_start` — investigated twice (v0.9, v0.10), a real
  MLT bug now pinpointed, not just an unconverged design**: MLT's own
  masking pair (Meltytech, LGPLv2.1) - `mask_start` snapshots the frame,
  one or more filters in between write to the alpha channel, `mask_apply`
  composites the current frame over the snapshot through a transition
  using that alpha as the mask. kdenlive's own "Alpha shapes (Mask)"
  effect is exactly this pairing with `frei0r.alphaspot` as the shape
  filter - the same filter this project already drives for `reveal:
  iris`.

  **v0.9** confirmed the bracket is real and safe on both melt builds
  (no `crop`-style crash), and found the alphaspot shape *protects* the
  marked zone rather than spotlighting it - the encapsulated filter's
  effect shows *outside* the shape. **v0.10** resolved that: `Operation`
  (`frei0r.alphaspot`'s `filter.9`) controls the polarity - `0` ("Write
  on clear", the default) protects the shape and filters everything
  else, `1` ("Subtract") inverts it. Confirmed with real renders,
  isolating that one variable. The bracket also composes cleanly with a
  real shipped effect (`color_grade:`'s own `lift_gamma_gain`), verified
  through the actual generator's own output, not just a standalone
  probe.

  **The real blocker, found in v0.10**: `mask_start` forwards its
  `filter.*` properties to the `frei0r.alphaspot` instance it builds
  internally via `mlt_properties_pass(instance_props, properties,
  "filter.")` (`~/dev/mlt/src/modules/core/filter_mask_start.c`), but
  passing **any** value for `filter.1`/`filter.2` (Position X/Y) through
  that path - whether using kdenlive's friendly names or the same
  numeric indices this project already validates for `frei0r.alphaspot`
  elsewhere (`_add_reveal_style_filter`, `reveal: iris`) - collapses the
  shape to a small fixed patch in the top-left corner, ignoring the
  requested value entirely. Isolated with a dedicated test:
  `filter.1=0.7, filter.2=0.3` (should place the shape top-right)
  produced the exact same degenerate patch as `filter.1=0.5,
  filter.2=0.5`. With no overrides (alphaspot's own internal defaults),
  the shape appears reasonably centered - so the filter itself works;
  it's specifically property forwarding through `mask_start` that
  breaks positioning. Ruled out: aspect ratio (tried a square 240x240
  canvas too, same result) and multi-threaded render non-determinism
  (tried forcing single-frame renders). Leading unconfirmed theory:
  `Position X`/`Size X`/etc. are `animated`-type params in frei0r - they
  may need the `"0=value"` keyframe format even for a constant value
  when forwarded via `mlt_properties_pass`, unlike when set directly via
  XML (`_add_reveal_style_filter`'s approach) - not confirmed within
  reasonable effort this round.

  Without reliable position/size control, a `mask_rect:`-style field
  still isn't honestly implementable - "mask at alphaspot's fixed
  default position/size, no control" is too degenerate to ship. A third
  round should start by diagnosing `mask_start`'s `animated`-property
  forwarding directly - try passing `filter.1` as `"0=0.7"` instead of
  `"0.7"` before anything else.
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

## Asked for, not started (2026-10-22)

- **`video-as-code docs`**: the documentation as a local web - README,
  `docs/project-yaml.md` and the rest rendered to HTML - with the
  gallery as part of it, each key in the reference linking to its card
  and each card to its key; built on the user's machine (their melt,
  their plugins: no licence trouble with OpenShot's maps or Ubuntu's
  photos), the README staying as GitHub's front page. Its own section
  in the README saying what it builds and where. Markdown to HTML via
  an optional `markdown` dependency, or a small converter of our own.
- **Effects from libopenshot** (its Wave, AnalogTape, Sphere,
  LensFlare...): as a `derive:` operation that renders a clip through
  libopenshot (python3-openshot, Python 3.14, run as a separate
  process like Real-ESRGAN), since a frei0r/MLT bridge would mix Qt 5
  (libopenshot) and Qt 6 (MLT) in one process. Its transitions are
  covered already (`luma: openshot/<name>`).
- **Single-clip GLSL filters**: the same kind of frei0r plugin as
  `frei0r.gltransition`, as a filter (Shadertoy-style glitch, CRT,
  waves...).
- **`lut:` from a LUT pack by name**, and a gallery card per LUT found
  next to the project.

## Other rendering engines

- **libopenshot as a second engine, combined with melt** (raised
  2026-10-13): libopenshot (OpenShot's C++ library, with Python bindings)
  has its own effects and keyframe model, some of which melt lacks. Two
  shapes, very different in size:
  - *Per-clip effects through `derive:`* (the likely first step): a
    `derive:` operation that runs a clip through libopenshot with an
    effect applied and hands the result to melt as an ordinary clip -
    the same "generate a file, cache it under `.derived/`" path `upscale:`
    (Real-ESRGAN) and ffmpeg's operations already take. melt keeps doing
    the composition (tracks, transitions, fades, speed, titles), so
    both engines' effects can end up in one video without the two
    timeline models having to agree on anything.
  - *A full alternative backend*: translating the whole timeline model
    to libopenshot's timeline as well as to MLT XML, and keeping both
    translations in step. Large; only worth it if melt turns out to be
    the limit for something structural, not for a missing effect.
  Open question before either: which effects, concretely, melt doesn't
  have and libopenshot does - that decides whether this is worth doing.
- **Other open-source engines and tools, by how they'd fit** (raised
  2026-10-13, from memory - none tried here yet; each needs checking
  before it's scoped):
  - *Full editing engines - alternatives to melt as a backend*:
    - **GStreamer Editing Services (GES)**, Pitivi's engine: layered
      timeline, transitions, effects (the same frei0r plugins melt
      uses), Python bindings. The most serious candidate for a second
      full backend.
    - **Plain ffmpeg filtergraphs**: already what `derive:` runs;
      `xfade` has ~50 transitions and there are hundreds of filters.
      Light and everywhere, but a complex timeline becomes an unwieldy
      graph.
    - **Blender's Video Sequence Editor**: fully scriptable from Python
      (`bpy`) and renders headless; heavy, but brings node compositing,
      3D titles and strong keyframe animation.
  - *Specialised tools - would fit as `derive:` operations, like
    `upscale:` (Real-ESRGAN)*:
    - **RIFE**: AI frame interpolation for smooth slow motion - the
      remedy for the "only a blend-based approximation" slow-motion gap
      above. Probably the most useful of all for home videos.
    - **VapourSynth**: Python-scripted video processing - denoising,
      advanced stabilisation, deinterlacing, interpolation (MVTools).
    - **Natron**: node-based VFX compositing (keying, rotoscoping,
      tracking).
  - *Code-built video, a different style - for titles and motion
    graphics, generated as clips*:
    - **Motion Canvas** and **Revideo** (MIT), and **Remotion**
      (source-available, not fully open source): videos written in
      TypeScript/React; strong at animated titles and graphics.
    - **Editly**: declarative JSON videos on top of ffmpeg - close to
      this project's own idea, worth reading for inspiration more than
      integrating.
    - **MoviePy**: Python compositing on ffmpeg; flexible but slow.
  Likely order if any of it happens: RIFE, then Motion Canvas or Blender
  for richer titles - all three through `derive:`, no engine change -
  and GES only if a second full backend is ever wanted.

## Disk use of fetched sources

With every source in a shared album (`source:`, `url:`, `derive:`), the
local sources folder is really a cache of the album, and a project with
a year of phone videos easily takes tens of GB. One file per album item
and `fetch --prune` shipped in v0.11 (see ROADMAP.md); still open:

- **A cache-cleaning command**: list, and on request delete, files in
  the sources folder that no bin clip uses any more (old hand-made cuts
  and clips dropped from the timeline) and stale outputs in `.derived/`
  whose spec no longer exists - with a "not in the album" warning for any
  file that `fetch` couldn't bring back.

## Performance

Measured on a real project: a ~4 min 1080p video with ~150 clips and a
long chain of crossfades, previewed at 480x270 on a 4-core laptop with
7 GB of RAM and an integrated GPU.

- **Parallel downloads in `fetch`**: items are downloaded one at a time;
  ~150 of them took over half an hour on a fresh machine. 3-4 at once
  should help, within whatever rate limits Google Photos applies.
- **Hardware encoding/decoding for `derive:`** (VAAPI on Intel/AMD,
  NVENC/NVDEC on NVIDIA): much faster for long inputs (light copies,
  60 fps phone videos), at some quality cost - so for preview copies
  rather than final outputs, and only when the device supports it.
- **Generating several `derive:` clips at once**: ffmpeg already uses
  every core for most encodes, so on a 4-core machine with little RAM
  the gain is small (maybe x1.5); last in line.
