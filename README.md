# <img src="docs/logo.svg" alt="" width="48" height="48" align="absmiddle"/> video-as-code

Describe a video in a YAML file and render it straight to video with
`video-as-code render` - through melt, the MLT engine
[kdenlive](https://kdenlive.org) renders with. The YAML is what you
check into a VCS (like Git), so `git diff` shows what changed in the
edit. To keep editing by hand, `video-as-code build` gives a `.kdenlive`
to open in kdenlive (see *What you see in kdenlive* in
[How to make a video](docs/making-a-video.md#what-you-see-in-kdenlive)),
and `video-as-code import` turns a `.kdenlive` back into a YAML.

These docs, with a gallery where each effect and transition plays, are
also at <https://video-as-code.juan-ramirez.es/>.

## Install

```bash
pip install -e ".[dev]"
```

`video-as-code reconstruct` (see below) needs the optional `reconstruct` extra
(`numpy`, `Pillow`) on top of that:

```bash
pip install -e ".[dev,reconstruct]"
```

`video-as-code docs` needs the optional `docs` extra (Python-Markdown):
`pip install -e ".[docs]"`.

Then `video-as-code doctor` says what this machine has of what
video-as-code uses - melt, ffmpeg, each MLT module a feature needs
(titles, compositing, frei0r, Lottie, stabilization...), the plugins it
builds and the optional tools - and, for what's missing, how to get it.
A module missing from melt doesn't stop it: what uses it renders as
nothing, silently, so it's worth running on a new machine. It exits 1
only when something nothing renders without is missing.

The rest of `video-as-code` (`build`/`validate`/`import`) only ever needs PyYAML —
`reconstruct.py` is imported lazily, so skipping this extra doesn't affect
anything else.

## Usage

```bash
video-as-code render project.yaml [-o output.mp4] [--split] [--part P]  # render straight to video via melt
video-as-code render project.yaml --from 1:20 --to 1:35  # just that stretch, to project_1m20s-1m35s.mp4
video-as-code watch project.yaml [--proxy WxH]          # a preview again on every save, played from what changed
video-as-code validate project.yaml                     # validate without rendering
video-as-code build project.yaml [-o output.kdenlive]   # a .kdenlive, to open in kdenlive
video-as-code build project.yaml --format openshot      # an OpenShot project (.osp) instead
video-as-code import project.kdenlive [-o output.yaml]  # existing .kdenlive -> YAML
video-as-code fetch project.yaml [--verify] [--prune]   # download source:/url: clips
video-as-code transitions                               # every wipe shape a luma: can name
video-as-code schema [-o schema.json]                   # the YAML's JSON Schema, for an editor
video-as-code gallery [--with photo.jpg]                # every transition and effect, in the browser
video-as-code docs [--with photo.jpg] [--public DIR]    # this documentation as a local web, the gallery in it
video-as-code install-gl-transitions                    # build the plugin transition: gl needs (once)
video-as-code doctor                                    # what's installed of what it uses, and how to get the rest
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

### `video-as-code watch`: a preview on every save

```bash
video-as-code watch project.yaml
```

renders a light preview (from 480x270 copies of the videos, as
`--proxy`), opens it in a player, and does it again every time the YAML
- or any other YAML file in its folder, its parts and presets - is
saved: the player opens again a second before the first frame that
changed. Only what changed is rendered again: the preview is rendered
in short segments (8 s at most), each cached by what it plays, as
`render --split` does, so after the first time an edit costs the few
seconds of video it touches. Where in them the picture actually changed
is found frame by frame, comparing them with the last render.

A save that doesn't validate is reported, and the last preview stays.
`--proxy WxH` sets the copies' size, `--full` uses the original files,
`--part P` watches one part of a project in several files and
`--no-play` only renders (and says where it changed). The player is the
first of mpv, vlc and ffplay found, or a command of your own in
`VIDEO_AS_CODE_PLAYER` - `{file}` and `{start}` (seconds) replaced:
`VIDEO_AS_CODE_PLAYER="mplayer -ss {start} {file}"`. Its cache
is its own (`.render_cache/watch/`), apart from a full render's.

### Rendering just a stretch: `--from` and `--to`

```bash
video-as-code render project.yaml --from 1:20 --to 1:35
```

renders only those 15 seconds, at full quality - to look at a transition
or a cut as the final video will have it, without rendering all of it.
Times are seconds (`80`, `80.5`) or minutes:seconds (`1:20`, `1:20.5`,
`1:02:03` with hours); either may be left out (from the start, to the
end). The file is `project_1m20s-1m35s.mp4`, so as not to replace the
whole video's (`-o` names it). melt is given a copy of the project with
only the clips that stretch plays, so it starts as fast as a short
project does; with `--split`, the segments inside the stretch are the
whole render's own, shared with its cache. The frames are the ones the
whole render has there; the sound, though, starts at the stretch, so an
effect that builds up over time (an echo, a compressor) starts afresh.

### Writing the YAML in an editor: `video-as-code schema`

The project file has a JSON Schema: an editor that knows it completes
keys and values as you type, shows what each key means (the text of
`docs/project-yaml.md`) and underlines a typo - `fade_inn`, `reveal:
irs`, a gl-transition's parameter past its range - as you write it, not
when `validate` runs. In VS Code, install Red Hat's *YAML* extension and
start the project file with this line (any editor with
yaml-language-server reads it too):

```yaml
# yaml-language-server: $schema=https://video-as-code.juan-ramirez.es/project.schema.json
```

`video-as-code schema -o schema.json` writes the schema of the installed
version, for a line that points at that file instead (offline, or to
match an older version). It checks each key as it's written; whether a
file exists, a clip fits its source or two clips overlap is still
`validate`'s - and so are melt's services and libopenshot's effects,
which depend on what's installed.

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
part alone; `fetch`, `--proxy` and `--split` work on each part, and
`import` brings a project of parts back as files. See
`docs/project-yaml.md`.

### Lottie animations

A Lottie animation (`.json`, or Telegram's `.tgs`) goes in the bin like
any clip and plays, with its transparency, over the tracks below -
animated titles, stickers, overlays from LottieFiles. melt's
`glaxnimate` module plays it (the distributions' melt has it).

### `glsl:`: GLSL effects on a clip

`glsl: crt` (or `chromatic`, `wave`, `glitch`, `kaleidoscope`, `vhs`, or
a `.glsl` file of your own with `vec4 effect(vec2 uv)`) runs a shader
over a clip's picture on the GPU, with `time` and `progress` to animate
it - through the plugin `video-as-code install-gl-transitions` builds.
See `docs/project-yaml.md`.

### libopenshot's effects: `derive: {openshot: [...]}`

OpenShot's effects - Wave, AnalogTape, LensFlare, SphericalProjection,
Pixelate... and its audio ones (Echo, Robotization...) - applied to a
clip when it's generated, by libopenshot itself:

```yaml
- id: wavy
  derive:
    path: media/sea.mp4
    openshot: [{effect: Wave, amplitude: 0.6, wavelength: {0: 0.06, 3: 0.2}}, {effect: AnalogTape}]
```

It needs `python3-openshot` (which `openshot-qt` brings); it runs in a
process of its own, with the system's Python. Two of its effects are
refused, saying why: `Caption` draws nothing outside OpenShot's own
editor (a title clip does that), and `Mask` needs a mask picture it
can't be given here. See `docs/project-yaml.md`; `video-as-code gallery`
shows each one, over the sample video or sound.

### An OpenShot project: `build --format openshot`

`video-as-code build project.yaml --format openshot` (or `-o
project.osp`) writes the timeline as an [OpenShot](https://www.openshot.org)
project instead of a `.kdenlive`, to finish the video by hand there. It
needs `python3-openshot` too: the project is written by libopenshot
itself, so it's in the shape the installed OpenShot expects.

What carries over: the files; each track as a layer (the last track on
top); each clip's trim and place; fades, as the clip's opacity (picture)
or volume (sound), with `gain` and `mute`; dissolves; wipes, as
OpenShot's own transition - a mask over the same grayscale picture,
darker parts first, either way and `reverse`d, `softness` as its
contrast (approximately) - with `luma: openshot/<name>` using OpenShot's
own file; and guides, as markers. A `derive:` of libopenshot's effects
alone (`openshot:`, no other operation) arrives as its input with the
effects on it, editable in OpenShot - the reverse of kdenlive, where it's
the generated file.

The rest has no counterpart in OpenShot and is left out, each thing
named in a warning: melt's own effects and transitions (`melt:`,
`glsl:`, `transition: gl`, `affine`, `film_*`, `color_grade`,
`ken_burns`...), `speed`, position and size, title clips, Lottie
animations and parts. Checked by playing the result with libopenshot's
own timeline - what OpenShot plays and exports with - against melt's
render (`tests/test_osp.py`).

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
option hides those. One that does is shown with other values instead
(each ranged parameter toward one end, then the other, up to five
tries), and one that can't render here says why in a line - melt
crashing on it, ffmpeg unable to set it up - with the whole error
folded under it. Filters that only measure (levels, loudness,
silences) are listed apart, without a preview. melt is asked about its services once per version
(`melt-catalog.json` in the cache), and their previews depend on melt
and the samples only, so a change to this project doesn't render them
again.

The first run renders ~40 previews and makes the wipe shapes'
thumbnails (a minute or two); everything is cached in
`~/.cache/video-as-code/gallery/`, so it then opens at once, and after
an update of the tool or of `melt` only what changed is rendered
again. `--no-open` only writes the page and prints where it is.

### `video-as-code docs`: this documentation as a local web, with the gallery

```bash
video-as-code docs [--with photo.jpg] [--no-open]
```

Builds this README and the guides in `docs/` (making a video, the
project YAML reference, reconstructing) as HTML pages, with the
gallery as one more of them, and opens them in the browser. The two are
linked both ways: each key of the reference that has cards in the
gallery (`transition`, `luma`, `lut`, `glsl`, `melt`, `title_style`,
derive:'s `openshot`...) has a ▶ to its first card, and each card names
its key, linked to its row in the reference.

It's built on your machine, not published: the gallery's previews are
renders with your `melt`, your plugins and your wallpapers, and the
pages come from the Markdown of this checkout (a `pip install` without
the source has no docs to build). Pages go to
`~/.cache/video-as-code/docs/`, next to the gallery's
`~/.cache/video-as-code/gallery/`; the gallery is rendered and cached as
`video-as-code gallery` does. `--with` and `--no-open` as there. Needs
the `docs` extra (see *Install*). This README stays as it is - the
project's front page on GitHub - and is the docs' first page.

#### Publishing them: `--public` and GitHub Pages

```bash
video-as-code docs --public site/
```

writes, instead, a copy to publish in `site/`: the pages, the gallery
and only the previews it shows, an `index.html` that goes to the docs
and a `.nojekyll` (for Pages to serve the files as they are). It's
built apart, in `~/.cache/video-as-code/public/` - its first run renders
the whole gallery again - and made to be shared: the previews play over
generated pictures, not the wallpapers' photos (each under its own
licence); OpenShot's 412 wipe shapes (GPL-3.0) and your own LUTs are
left out; `--with` isn't allowed. Each page says when and with which
melt it was built. The folder must be new, empty (but for a `.git` or a `CNAME`) or one `--public`
wrote before (its contents are then replaced).

To serve it with GitHub Pages from a `gh-pages` branch (~55 MB of
previews: kept off `master`, whose history doesn't grow with them),
write it straight into a worktree of that branch - `--public` leaves
its `.git` alone:

```bash
git worktree add --orphan -b gh-pages ../video-as-code-pages   # once; later: git worktree add ../video-as-code-pages gh-pages
video-as-code docs --public ../video-as-code-pages --no-open
cd ../video-as-code-pages && git add -A && git commit -m "docs: rebuilt" && git push origin gh-pages
```

and once, in the repository's *Settings → Pages*: deploy from the
`gh-pages` branch, its root. The docs are then at
`https://<user>.github.io/video-as-code/`.

On a domain of your own instead (or if `<user>.github.io` already
redirects to one that isn't served by GitHub): point it at Pages with a
DNS record `CNAME docs.example.org → <user>.github.io` and give it in
*Settings → Pages → Custom domain*. GitHub writes it to a `CNAME` file
in `gh-pages`; `--public` leaves that file alone too, so the domain
survives each rebuild (`git pull` in the worktree first, to have it).

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

## Credits

- **Juan Ramírez** ([juan-ramirez.es](https://juan-ramirez.es/)): the
  idea, where it goes and what it should do, and trying it all on his
  own videos.
- **Claude** (Anthropic), through
  [Claude Code](https://claude.com/claude-code): a large part of the
  code, the tests and these docs, pair-programming with Juan - most of
  the project's commits are co-authored by it.
- Bundled from others: [gl-transitions](https://gl-transitions.com)'
  shaders (see `src/video_as_code/gltransitions_data/LICENSE`) and
  GitHub's mark in the docs' bar ([Octicons](https://primer.style/octicons/), MIT).

## License

MIT - see `LICENSE`. The tools it drives are separate programs with their
own licenses, run as they are, not bundled: melt/MLT (LGPL), kdenlive
(GPL), ffmpeg (LGPL/GPL), yt-dlp (Unlicense) and Real-ESRGAN's
realesrgan-ncnn-vulkan (MIT; its models BSD-3-Clause), which `fetch`
offers to download from its own releases.
