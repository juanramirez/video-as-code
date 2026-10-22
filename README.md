# video-as-code

Programmatic generator for [kdenlive](https://kdenlive.org) projects from a
declarative YAML description, meant for versioning videos with a VCS (like
Git): the `.yaml` is what you check in; the `.kdenlive` (XML/MLT) is
regenerated deterministically with `video-as-code build` and doesn't need to live
in the repo.

## Install

```bash
pip install -e ".[dev]"
```

`video-as-code reconstruct` (see below) needs the optional `reconstruct` extra
(`numpy`, `Pillow`) on top of that:

```bash
pip install -e ".[dev,reconstruct]"
```

The rest of `video-as-code` (`build`/`validate`/`import`) only ever needs PyYAML —
`reconstruct.py` is imported lazily, so skipping this extra doesn't affect
anything else.

## Usage

```bash
video-as-code build project.yaml [-o output.kdenlive]   # generate the .kdenlive
video-as-code validate project.yaml                     # validate without building
video-as-code import project.kdenlive [-o output.yaml]  # existing .kdenlive -> YAML
video-as-code render project.yaml [-o output.mp4] [--split] [--part P]  # build + render straight to video via melt
video-as-code fetch project.yaml [--verify] [--prune]   # download source:/url: clips
video-as-code transitions                               # every wipe shape a luma: can name
video-as-code gallery [--with photo.jpg]                # every transition and effect, in the browser
video-as-code install-gl-transitions                    # build the plugin transition: gl needs (once)
```

`build` and `render` also take `--fetch`, to run `fetch` first, and
`--proxy WxH` (e.g. `--proxy 480x270`) for a preview from light copies -
see below.

`video-as-code render` needs `melt` (the MLT engine kdenlive itself renders with) on
PATH — it's not bundled with `video-as-code`. It's equivalent to running
`video-as-code build` and then `melt output.kdenlive -consumer avformat:output.mp4`
yourself, minus the intermediate `.kdenlive` file (built to a temp
directory and discarded); `-o`'s folder is created if it doesn't exist. `VIDEO_AS_CODE_MELT` points at another `melt`
- e.g. an MLT built from source, newer than the distribution's (set
`MLT_REPOSITORY` for its plugins, as melt itself expects).

`render --split` is for machines without the RAM to render the whole
project in one melt run (melt loads every clip up front and keeps every
decoded photo until it exits). The picture is rendered in segments, each
from a copy of the project trimmed to the clips it touches, and joined
without re-encoding; the sound is rendered in one pass over the whole
length (in segments, a time-stretched song would click at every seam) and
muxed in. Segments are as long as an estimate of their memory allows -
long where few clips play, short where crossfades pile up - within
`--max-mem` MB each (default: 60% of the memory available when it
starts), and each melt runs in a memory-capped `systemd-run --user
--scope` when available, so a wrong estimate kills that melt, not the
session. Every segment's render is cached in `.render_cache/` next to the
YAML, keyed by its trimmed project and the files it reads: after an edit
only the segments it touches are rendered again.

`video-as-code import` recognizes everything this generator knows how to produce
(video/audio tracks, trimmed clips, fade to/from black, volume fades,
per-clip mute, audio pan, speed changes (forward or reverse), title
clips, guides/markers, track names/hidden/locked/audio-muted state, bin
folders, project notes, cross-track `wipe`/`affine` transitions (into a
low-level `transitions:` key), `iris`/`flip`/`glitch`/`delaygrab`/
`cairogradient` reveal/burst filters, chroma-key, burned-in text
overlays, Ken Burns pan+zoom, focus-pull rects, `qtblend`
picture-in-picture compositing (static or animated via
`position_keyframes:`), edge crop, freeze-frame, the "old film"
filter pack, lift/gamma/gain color grading, and video stabilization).
Anything it doesn't recognize isn't discarded: it's kept as-is under
`x_kdenlive_raw:` in the output YAML so you can decide by hand what to
do with it. Speed ramps (`speed_ramp:`) are a known exception — a
dense per-frame keyframe map can't be cleanly inverted back to its
original breakpoints, so those fall to `x_kdenlive_raw:` too,
deliberately.

See `examples/simple/` for a minimal working project (tiny generated
assets, one audio + one video track, fades, trims, a speed change, mute,
and a title clip): `video-as-code build examples/simple/project.yaml`. See
`examples/scenes/` for the high-level `scenes:` compiler (a cut, a
`dissolve`, and a `wipe`, all from the same two tiny images):
`video-as-code build examples/scenes/project.yaml`.

See `docs/making-a-video.md` for a short step-by-step howto of this
whole flow, and `docs/project-yaml.md` for every key a project file
accepts.

### `--proxy`: light previews of only the parts used

Full-resolution phone videos (HEVC, 1080p and up) are what makes `melt`
run out of memory on a laptop - it builds every clip's producer at load -
and slow even when it doesn't. With `--proxy 480x270`, `build`/`render`
read each video from a light H.264 copy of **only the source ranges the
timeline uses** (ranges less than 5 s apart share a copy, far-apart uses
of one long video get one copy each, each with a 1 s margin), with the
placements' source times shifted to match. The copies are cached in
`.proxies/` next to the YAML (add it to `.gitignore`), so later previews
skip straight to rendering.

- `crop:` margins are scaled to the copy's size; colours stay as the
  original's (a colour-tagged video keeps its tags; an untagged HD one
  has its colour matrix converted, since MLT guesses it by height).
- Stills, gifs and audio files are used as they are; so is a video
  played backwards (negative `speed:`).
- With flat `speed:`, where a clip starts in its copy is rounded to a
  frame of the warped clip - up to `speed / fps / 2` source seconds off.
  That's invisible in a preview, and why this is for previews: render
  the final video without `--proxy`.
- Every copy starts near the part that's used, so the freeze MLT's
  timewarp producer has with a large `source_in` plus `speed:` doesn't
  happen in a proxied render.

The project's own `width`/`height` don't change: for a lighter preview,
render a copy of the YAML with a smaller canvas too.

### `video-as-code fetch`: assets from a Google Photos shared album

If the material for a video lives in a Google Photos album, share it by
link and point the project at it; bin clips can then name an album item
by its **original file name** instead of a local `path:`:

```yaml
project:
  # ...
  sources:
    album: https://photos.app.goo.gl/...   # the album's share link
    dir: assets                            # where to download (default: assets)
bin:
  - id: slide          # the readable name the timeline uses, as always
    source: PXL_20250830_085416309.mp4
```

`video-as-code fetch` downloads what's missing into `dir`, at original
quality, as `<capture date>_<id><ext>` (`20250830_slide.mp4` above; set
`local_name:` on the clip for a different one), and records each item in
`sources.lock.yaml` next to the YAML (album item id, size, sha256,
capture time - machine-written, meant to be committed, never edited by
hand) so later fetches go straight to it. Renaming a clip's `id` renames
its local file on the next `fetch` instead of downloading it again. `build`/`render`
never download on their own: a `source:` clip that isn't on disk yet is a
validation error pointing at `fetch` (or `--fetch`). Add `.fetch_cache/`
(file names already looked up in the album) to your `.gitignore`.

A file name doesn't always pick one album item: cameras restart their
numbering, and WhatsApp names pictures after the day they arrived
(`IMG-20150308-WA0003.jpg` can be several different photos from several
phones). With a plain name, `fetch` takes the item taken closest to the
time in its name - which may be the wrong one. Add its capture time to
say which:

```yaml
  - id: beach
    source: {name: IMG-20150308-WA0003.jpg, taken: "2015-03-08 15:23:51"}
```

`taken` is the capture time as Google Photos has it, local to where it
was taken, to the second (the same value `sources.lock.yaml` records).
If even that ties - a race photographer's site can hand out different
pictures under the same name and time - add `size: [w, h]`, the item's
size in pixels (one of the two is enough when it alone tells them apart).
And if even the size ties - two different pictures saved again on the
same day can share name, time and size - add `id:`, the album item's own
id (the `media_id` that `sources.lock.yaml` records for it, `AF1Qip...`):

```yaml
  - id: cake
    source: {name: 2023-04-28.jpg, id: AF1QipExampleExampleExampleExampleExample}
```

It names exactly one item, so it's enough on its own; `fetch` still checks
that the item's name (and `taken`/`size`, if given) match, and reports it as
not found otherwise. The id only lasts while the item stays in the album:
removed and added again, it gets a new one - which is why name, time and
size remain the usual way.
That's what keeps such a project buildable from its YAML alone: without
it, the only way to point at the right one would be a local copy of the
file, which a fresh checkout doesn't have. In the lock and on disk the
item is then known as `<name>@<taken>#<w>x<h>!<id>` (each part only if
given), so two same-named items never overwrite each other.

The album can also be given by **name**, before it exists:

```yaml
  sources:
    album:
      name: "vid · summer · 2014"                 # what to call it
      url: https://photos.app.goo.gl/...       # its share link, once it has one
```

That's for a tool that builds the album itself - finding the clips in a
photo library, putting them in a new album with that name and sharing it
- and then writes the link in as `url:` (`albums.write_album_url`, a text
edit that keeps the YAML's comments and layout). Until it has a `url:`,
`fetch` says the album doesn't exist yet instead of downloading. Keep the
album afterwards: `fetch` needs it whenever a file is missing - on another
machine, or after pruning (below) - and an album takes no extra space,
since its items are the library's own.

A project that remakes an old video can name that video too, so `fetch`
brings it along (it's what the remake is compared with, and what shots
still missing from the album are cut out of meanwhile):

```yaml
  sources:
    reference:
      url: https://youtu.be/...                 # e.g. an unlisted upload
      path: reference/Old edit.mp4              # default: reference/reference.mp4
```

It's downloaded whole - from YouTube, video and sound merged into an mp4 -
once; delete the file to download it again. `build`/`render` don't need it
unless a clip cuts from it. `url:` can be a list, tried in order; YouTube
re-encodes its videos now and then, so while shots are cut from it, put an
exact copy first (e.g. a Google Drive link to the file the project was
made with) and YouTube after it. Its size and sha256 go in the lock's
`reference:` section, and `fetch` says so when a download isn't that file.

What isn't in the album - typically the music - can come from links
instead, tried in order until one works:

```yaml
  - id: music
    url:
      - https://www.youtube.com/watch?v=...          # YouTube: the audio only
      - https://drive.google.com/file/d/.../view     # fallback: the same file on Drive
```

`fetch` stores it as `<id><ext>` (`music.m4a`; `local_name:` works here
too) and records which link it came from, with its size and sha256, in
the lock's `links:` section. From YouTube it takes the m4a audio stream
as served, through the external `yt-dlp` - which breaks every time
YouTube changes, so keep it current (a distro's package usually isn't;
`VIDEO_AS_CODE_YT_DLP` sets the command to run, e.g.
`"python3 ~/bin/yt-dlp --js-runtimes node"`). That fragility is what the
fallback is for: upload the very file YouTube gave you to Drive, shared
as "anyone with the link", so both links yield the same audio. Timings
cut to the music depend on it: YouTube's other audio streams can start
tens of milliseconds off.

When something can't be fetched - a name the album doesn't have, a
download that broke, a clip none of whose links work - `fetch` still
gets everything else, and then, in a terminal, asks about each problem:
retry it, give a link to download it from instead, name the album item
it really is, use a file from this machine (only there: elsewhere it'll
still be missing) or skip it. A link or a new name is written straight
into the YAML (a text edit that keeps comments and layout), and `fetch`
runs again for what's still missing. Outside a terminal it just fails,
listing everything that went wrong.

Two things to keep in mind: anyone with the share link can see the whole
album, so keep projects that contain one in a private repository; and
this reads the album's public web page rather than an official API (the
Google Photos APIs no longer allow re-downloading a user's media
unattended), so a change on Google's side can break it until
`gphotos.py` is updated.

#### `derive:`: clips generated from another file

A cut, a reversed gif or a louder copy doesn't need to be stored: describe
it and it's generated with ffmpeg.

```yaml
bin:
  - id: jump
    derive:
      source: PXL_20250830_085416309.mp4   # or path: a local file, or clip: another bin id
      trim: [12, 16]                        # source seconds
      fps: 30
  - id: jump_back
    derive: {clip: jump, reverse: true}
```

Operations, applied in this order: `trim: [start, end]` and `cut:
[[start, end], ...]` (ranges removed from the middle, picture and sound
alike - both in the input's own seconds), `crop: {x, y,
w, h}` (source pixels), `scale: [w, h]` (`-1` on one side keeps the aspect
ratio), `devignette:` (undoes a vignette baked into the input, such as a
shot taken from an old edit: how bright its corners are relative to its
centre, e.g. `0.81`), `fps:`, `reverse: true` (picture and sound) and `volume:` (audio
gain factor) and, last, `upscale: 4`. A gif stays a gif, a still image
becomes a `.png`, audio an `.m4a` and video an `.mp4` (h264 + aac, keeping
the sound if there is any).

`motion: true` takes the short video a phone's motion photo (Google's
`.MP.jpg`) carries inside it instead of the photo, so the result is an
`.mp4`; the other operations then apply to that video.

`intra: true` encodes a video result with every frame a keyframe. MLT's
`speed:` (timewarp) can stall for a moment, or fail, near the end of a
file with long keyframe intervals; an all-intra file avoids that, at the
cost of a bigger file. It only affects video outputs.

`upscale: 4` enlarges a still or a gif x4 with Real-ESRGAN, an AI upscaler
that invents plausible detail rather than stretching pixels - for small
album items (Google Photos' own animations are ~800x600) that would look
blurry full screen. It runs `realesrgan-ncnn-vulkan` on any Vulkan
device, an integrated GPU included, but slowly there - a minute or two
per frame - so it's limited to stills and gifs (a gif's repeated frames
are upscaled once, and its per-frame delays are kept). `fetch` offers to
download it when a project needs it (or run `video-as-code
install-upscaler`: a pinned release, checked against its sha256, into
`~/.local/share/video-as-code/realesrgan/`); `VIDEO_AS_CODE_REALESRGAN`
points at another copy. Gifs need Pillow (`pip install
'video-as-code[upscale]'`).

The result is cached under `<dir>/.derived/` with the spec's hash in its
name: `build`/`render`/`validate` generate it once, offline, whenever the
input is on disk (and again only if the spec or the input itself
changes), and `fetch` downloads album inputs and generates right away. An
album item used only as a `derive:` input is still downloaded, under its
own name; one that a plain `source:` clip also uses is the same file,
downloaded once. The cache can always be deleted and rebuilt, so
`<dir>/.derived/` belongs in your `.gitignore` too.

`fetch --prune` then deletes the album items used only as `derive:`
inputs once everything made from them is generated - often big originals
of which a few seconds are used. The output's record of its input's
sha256, matching the lock's, is enough for `build`/`render`/`validate` to
keep using it, and `fetch` downloads the input again only if a `derive:`
made from it changes (or its output is deleted).

Every `render` also does that by itself, for files no render has needed
in a while:

```yaml
project:
  sources:
    album: https://photos.app.goo.gl/...
    prune_after_days: 7   # the default; 0 = never
```

Before rendering, it deletes what the tool can always bring back and
nothing this render reads: album items used only as `derive:` inputs
(once their outputs stand on their own, as above), album items nothing
uses any more, and generated files in `.derived/` and `.proxies/` - each
only if no `build`/`validate`/`render` has used it for that many days
(they keep a record in `.fetch_cache/last_used.json`). A file seen for
the first time just starts counting, and a file only on this machine
(one the lock doesn't know) is never touched. YAML files sharing a
sources folder (a project and its preview) share that record. `render
--no-prune` skips it once.

### OpenShot's transitions: `luma: openshot/<name>`

A `wipe` can also take any of the 412 transitions of
[OpenShot](https://www.openshot.org), on top of this project's own 12
shapes - `melt` itself ships none. They're grayscale masks, as MLT's
`luma` transition uses them, so they render in the same single melt
pass and open in kdenlive as an ordinary wipe:

```yaml
scenes:
  - transition: wipe
    luma: openshot/twirl_12
    luma_softness: 0.2     # softer edge, 0..1
    luma_reverse: true     # light parts first
    clips: [...]
```

They're not bundled (they're GPL-3.0+): they're read from openshot-qt
(`sudo apt install openshot-qt`), or from the folder
`VIDEO_AS_CODE_OPENSHOT_TRANSITIONS` points at, and each one used is
converted to PGM once, under `~/.cache/video-as-code/lumas-openshot/`.
`video-as-code transitions` lists every name, and `video-as-code
gallery` shows each one playing (see below). Like OpenShot, MLT
stretches each map to the frame - about half of them are 4:3.

What they add, by family (`<family>_<n>`, how many):
`twirl_*` (39), `distortion_*` (24), `luminous_boxes_*` (22),
`ray_light_*` (20), `deform_*` (18), `cross_*` (17), `board_*` (14),
`barr_ripple_*` (12), `wandering_*` (12), `boxes_*` (11), `future_*` (11),
`luminous_spiral_*` (11), `flower_*` (10), `fish-eyes_*` (9),
`fractal_*` (8), `postime_*` (8), `fogg_*` (7), `stretched_*` (5),
`hourglass_*`, `luminous_frame_*`, `mosaic_*`, `mozaic_*`, `ripple_*`,
`sunlight_*`, `whirpool_*`, `wipe_diagonal_*` (4 each), `hatched_*`,
`triangle_*` (3 each), and two each of `big_barr_shaking_*`, `checked_*`,
`clouds_*`, `frame_*`, `middle_barr_ripple_*`, `ray_light_left_*`,
`ray_light_right_*`, `right_mozaic_*`, `sphere_*`, `spiral_*`,
`spiral_abstract_*`, `spiral_big_*`, `spiral_small_*`, `stain_*`,
`star_*` and `strange_barr_*`. Then 84 single ones: arrows
(`left_arrow`, `middle_top_arrow`, `ripple_luminous_low_arrow`...),
bars and blinds (`big_barr`, `crossed_barr`, `blinds_sliding`,
`vertical_blinds_in_to_out`...), lozenges, mosaics, `clock_left_to_right`,
`wave_left_up` and its three siblings, `bubbles`, `puzzle`, `mountains`,
`spots`, `sun_shaking`, `dissolve`, `openshot_logo`... The 7 in
OpenShot's `common/` (`fade`, `circle_in_to_out`/`circle_out_to_in`,
`wipe_left_to_right` and the other three wipes) are what `dissolve`,
`radial`, `linear_x` and `linear_y` already do; `openshot/fade` is all
one shade, which MLT plays as a cut - use `transition: dissolve`.

### `melt:` and `melt/<service>`: any MLT filter or transition

For what there's no key of its own for: a clip's `melt:` takes any
filter the `melt` that renders it has, by service name - frei0r's ~100,
ffmpeg's as `avfilter.*`, MLT's own - and `transition: melt/<service>`
any of its transitions:

```yaml
- clip: beach
  start: 0
  duration: 4
  melt:
    - frei0r.cartoon: {diffspace: 0.4}
    - avfilter.hue: {av.h: "0=0;100=180"}
```

Values are written as they are, in MLT's own units and keyframe syntax.
An unknown service or property is an error, with the closest names, so
a typo doesn't render as nothing; `import` brings filters and
transitions it doesn't otherwise recognize back as these entries,
instead of `x_kdenlive_raw`, when the installed melt has them. See
`docs/project-yaml.md`.

### `transition: gl`: gl-transitions' 125 shaders

[gl-transitions](https://gl-transitions.com)' collection - cube, doorway,
page curl, morph, ripple, burn... - as scene transitions:
`transition: gl` with `gl: cube` (and, optionally, `gl_params: {...}`).
MLT doesn't run GLSL itself, so `video-as-code install-gl-transitions`
builds, once, a small frei0r plugin that does (EGL and GLES 3, no
window): melt then renders them in the same pass as everything else.
The shaders are bundled (MIT, two BSD; see
`src/video_as_code/gltransitions_data/LICENSE`). See
`docs/project-yaml.md`.

### Presets: `presets:`, `use:` and `presets_file:`

A set of keys used again and again - a transition with its duration, a
look - can be named once and used by name, anywhere the file has a map:

```yaml
presets:
  page: {transition: gl, gl: BookFlip, transition_duration: 0.8}
scenes:
  - use: page
    clips: [a.jpg, b.jpg]
```

`use:` takes a list too; keys written next to it win over the preset's;
a preset may use others. `presets_file: presets.yaml` brings in another
file's `presets:`, to share them between projects. See
`docs/project-yaml.md`.

### A project in several files: `parts:`

A long video can be split into files - a main one with `project:` and a
list of parts, each with its own `bin:`/`scenes:`/`timeline:`, and parts
may list parts:

```yaml
# trip.yaml
project: {id: trip, title: Trip, fps: 25, width: 1920, height: 1080}
parts:
  - intro.yaml
  - {file: mountains/all.yaml, transition: dissolve, transition_duration: 1}
```

Each part is a nested sequence in kdenlive; its paths are relative to
its own file, its bin ids are its own, and the main file's presets reach
it. `video-as-code render trip.yaml --part mountains/all` renders one
part alone. Not yet with `fetch`, `--proxy` or `--split`. See
`docs/project-yaml.md`.

### `video-as-code gallery`: every transition and effect, in the browser

Opens a page with everything a project YAML can ask for - the
transitions between clips, the wipe shapes, the clip effects, the
`derive:` operations and titles - each with the YAML that asks for it
(click to copy) and a preview of what it does. The previews are real
renders: each is a tiny project rendered by `video-as-code render`
itself, with the same `melt`, so what the page shows is what a render
gives, and one that no longer renders shows its error instead. They
play over photos of Ubuntu's own wallpapers when they're installed
(`/usr/share/backgrounds`, read there, not bundled), else over generated
pictures; `--with photo.jpg` (or a video) puts your own under them.
Two tabs keep transitions (between clips) and effects (on one) apart;
sections start folded, the links at the top open and jump to each one,
and a search opens those it finds something in.

Besides this project's own keys, the page has every filter and
transition the installed `melt` has (`melt:` and `melt/<service>`), at
their defaults: video ones over the pictures, audio filters over a
sample sound (a plucked arpeggio, to listen to), each with melt's own
description, and marked when at its defaults it changes nothing - an
option hides those. melt is asked about its services once per version
(`melt-catalog.json` in the cache), and their previews depend on melt
and the samples only, so a change to this project doesn't render them
again.

The first run renders ~40 previews and makes the wipe shapes'
thumbnails (a minute or two); everything is cached in
`~/.cache/video-as-code/gallery/`, so it then opens at once, and after
an update of the tool or of `melt` only what changed is rendered
again. `--no-open` only writes the page and prints where it is.

### `video-as-code reconstruct`: recovering a YAML from a rendered video

```bash
video-as-code reconstruct rendered.mp4 assets/ [-o output.yaml]
```

Unlike `video-as-code import`, this doesn't start from a `.kdenlive` project — it
starts from an already-**rendered** video (a flat `.mp4`, no edit
metadata) plus a folder of the source assets that presumably went into
it, and tries to recover which asset appears where, trimmed how, and with
what fades, by comparing pixel/audio content rather than parsing
structured XML. There is no guaranteed correct answer here (see
`ROADMAP.md` for how the matching works and its known limitations) —
segments it isn't confident about are never silently guessed; they're
kept under `x_reconstruct_unmatched:` in the output YAML for you to
review by hand, the same way `video-as-code import` handles `x_kdenlive_raw:`.

Treat its output as a rough first draft to check against the real
project, not a finished YAML.

See `docs/reconstructing.md` for a short step-by-step howto of this
whole flow.

### Language

All user-facing text (validation errors, CLI output, import warnings) goes
through a small message catalog (`i18n.py`), keyed by token and looked up at
call time. English is the source of truth and the fallback for any locale
or key that isn't translated. Set `VIDEO_AS_CODE_LANG=es` to get the Spanish
catalog:

```bash
VIDEO_AS_CODE_LANG=es video-as-code validate project.yaml
```

We deliberately don't read the system `LANG`/`LC_ALL`: locale selection is
opt-in and explicit, so behavior doesn't silently change across machines
and test runs stay deterministic. Code comments, docstrings, and this
README are always in English regardless of `VIDEO_AS_CODE_LANG` — that setting only
affects what a `video-as-code` *user* sees at runtime.

## Current status

Verified by the test suite (`pytest`):

- **YAML schema and validation** (`schema.py`): checks shape, types, that
  referenced assets exist on disk, that each clip's `in`/`out`/`duration`
  are consistent with the source file's real duration (via `ffprobe`,
  `probe.py`), and that clips don't accidentally overlap within a track.
  Collects all errors before raising, instead of stopping at the first one.
- **`.kdenlive` generation** (`xmlgen.py`): replicates the structure that
  kdenlive 24-26.x produces on save (per-track tractors, dual playlist for
  kdenlive's "mix" feature, project bin, fade to/from black on video,
  volume fades on audio, per-clip mute, audio pan/balance, speed changes
  (timewarp, forward or reverse, and multi-segment speed ramps via
  `timeremap`), a `boomerang:` shorthand that auto-expands a clip into a
  forward-then-reverse repeat (halving a held last frame at the turnaround,
  `boomerang_trim_hold:`), source-seconds trimming (`source_in:`/`source_out:`),
  dissolve/luma-wipe/affine (zoom, slide)/iris/flip/glitch/delaygrab/
  cairogradient transitions, chroma-key compositing, burned-in text
  overlays, Ken Burns pan+zoom, focus-pull (blur outside a rect),
  static or animated picture-in-picture (`qtblend`), edge crop,
  freeze-frame/hold, an "old film" filter pack (jitter/flicker/burn,
  dust, scratches, grain), heart/bowtie procedural wipe shapes,
  OpenShot's 412 transitions as wipe shapes (`lumas.py`),
  lift/gamma/gain color grading, video stabilization (`deshake`),
  title clips (`titles.py`, multi-line text), guides/markers, bin
  folders, track name/hidden/locked/audio-muted state, project notes,
  default `qtblend` filter). Deterministic output
  (IDs derived via `ids.py`, no hashes/thumbnails, which kdenlive
  recomputes on open anyway).
- **Reverse importer** (`reverse.py`): `.kdenlive` -> YAML, with a clean
  split between what's recognized and what isn't (`x_kdenlive_raw`).
- **`video-as-code reconstruct`** (`reconstruct.py`, optional `reconstruct` extra):
  rendered video + assets folder -> YAML, by content-based matching
  instead of XML parsing. See the *Usage* section above and `ROADMAP.md`
  for how it works and its known limitations.
- **CLI** (`cli.py`): `build` / `validate` / `import` / `render` / `reconstruct` / `transitions` / `gallery`.
- **Seconds<->frames conversion** (`timecode.py`): common NTSC framerates
  (23.976, 29.97...) as an exact fraction; deterministic round-half-up on
  exact ties (see note below).
- **i18n message catalog** (`i18n.py`): every user-facing string is a
  token, with `en` and `es` catalogs and English as fallback (see
  *Language* above).

### Note on time rounding

MLT/kdenlive always works in whole frames, never in floating-point seconds.
A value like `start: 0.5` at 25fps is 12.5 frames, which isn't exactly
representable: it has to be rounded. We use round-half-up
(`math.floor(x + 0.5)`) instead of Python's native `round()`
(round-half-to-even), because half-to-even is unpredictable in this domain
— it depends on the frame number's parity, not actual closeness, so 0.5s
could come back as either 0.48s or 0.52s after a round trip with no
intuitive pattern for whoever wrote the YAML. With round-half-up, exact
ties always round up: the precision loss is unavoidable (12.5 isn't a whole
number), but at least it's consistent and predictable. The 1-frame
tolerance in the round-trip tests (`tests/test_reverse.py`) accounts for
this limitation — it's not a test that was loosened carelessly.

## What's next

See `BACKLOG.md` for the unordered pool of known gaps and ideas, and
`ROADMAP.md` for what's shipped and what's scheduled for the next version.

## License

MIT - see `LICENSE`. The tools it drives are separate programs with their
own licenses, run as they are, not bundled: melt/MLT (LGPL), kdenlive
(GPL), ffmpeg (LGPL/GPL), yt-dlp (Unlicense) and Real-ESRGAN's
realesrgan-ncnn-vulkan (MIT; its models BSD-3-Clause), which `fetch`
offers to download from its own releases.
