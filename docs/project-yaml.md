# Project YAML reference

Every key a project file accepts, what it means and its default. For a
walk-through instead, see `docs/making-a-video.md`; for working files, see
`examples/simple/` and `examples/scenes/`.

Times are seconds (decimals allowed) unless said otherwise. Colours are
`[r, g, b, a]`, 0-255. A key left out takes its default; an unknown key
where only known ones make sense (the top level, `derive:`) is an error,
so a typo doesn't go unnoticed there.

An editor can check all this as you type, with the project file's JSON
Schema - see README's *Writing the YAML in an editor*:

```yaml
# yaml-language-server: $schema=https://video-as-code.juan-ramirez.es/project.schema.json
```

```yaml
project: {...}       # required: canvas, frame rate, where sources come from
bin: [...]           # the files (and titles) the video uses
timeline:
  tracks: [...]      # low level: every clip placed by hand
scenes: [...]        # high level: lists of clips with timing worked out
guides: [...]        # timeline markers
transitions: [...]   # wipe/affine transitions between two tracks
presets: {...}       # named sets of keys, used with use: (below)
presets_file: ...    # a file of presets shared by several projects
parts: [...]         # instead of the rest: the project in several files (below)
```

`timeline:` and `scenes:` can be mixed: scenes compile into extra tracks
before anything else runs.

Two more top-level keys are written by the tool itself and only kept, not
interpreted: `x_kdenlive_raw` (what `import` couldn't map, carried back on
`build`) and `x_reconstruct_unmatched` (the stretches `reconstruct`
couldn't match, for a person to review).

## `project:`

| Key | Default | Meaning |
|---|---|---|
| `id` | *(required)* | Short identifier of the project. |
| `title` | *(required)* | Human-readable title. |
| `fps` | *(required)* | Frame rate: `25`, `30`, `29.97`, `23.976`... |
| `width`, `height` | *(required)* | Canvas size in pixels. |
| `colorspace` | `709` | Colour matrix of the output (`709` HD, `601` SD). |
| `default_image_duration` | `4.0` | Length of a still image's producer. A still placed for longer than this is cut short at it: raise it if a still has to stay on screen longer. |
| `notes` | - | Free text, kept in the generated project. |
| `sources` | - | Where clips come from and how the downloaded files are kept (below). |

### `project.sources:`

| Key | Default | Meaning |
|---|---|---|
| `album` | - | A Google Photos shared-album link (`https://photos.app.goo.gl/...`), or a map `{name, url}`: the album's `name` (for a tool that creates it, then writes its link as `url`) and its `url` once it exists. Needed for any `source:` clip; `fetch` needs the link. Keep it out of public repos: anyone with it can see the album. See README's *`video-as-code fetch`* section. |
| `reference` | - | The video this project remakes, for a project that rebuilds an old edit: a link, a list of links (tried in order, as a clip's `url:`), or a map `{url, path}` - `path` being where to keep it (default `reference/reference.mp4`). `fetch` downloads it whole (from YouTube: video and sound, as an mp4) if it isn't there yet. Its size and sha256 are locked (`reference:` in `sources.lock.yaml`), and a download that isn't that file is reported: YouTube re-encodes now and then, so while shots are cut from it, list an exact copy first (a Google Drive link to the file) and YouTube after it. Only needed to find and match the old video's material, or while the project still cuts shots out of it (`derive: {path: reference/...}`); a new video has none. Keep unlisted links out of public repos. |
| `dir` | `assets` | Folder (relative to the YAML) where `fetch` puts downloads and `derive:` its outputs (in `<dir>/.derived/`). |
| `prune_after_days` | `7` | Before each `render`, delete files the tool can bring back by itself (album items only `derive:` needs, stale generated files) that no `build`/`validate`/`render` has used for this many days. `0` = never. See README's *`video-as-code fetch`* section. |

## `bin:`

A list of entries, each with an `id` (how the timeline refers to it) and
exactly one way to get its file:

| Form | Example | Where the file comes from |
|---|---|---|
| `path:` | `{id: beach, path: media/beach.jpg}` | A local file (relative to the YAML, or absolute). |
| `source:` | `{id: slide, source: PXL_20250830_085416309.mp4}` | An item of `project.sources.album`, by its original file name. `fetch` downloads it as `<dir>/<capture date>_<id><ext>`. When several album items share that name, `source: {name: IMG-20150308-WA0003.jpg, taken: "2015-03-08 15:23:51"}` - the name plus its capture `taken` time (local, to the second) - says which one; if even the time ties, `size: [w, h]` (in pixels) too; if even that ties, `id:` - the album item's id, the `media_id` in `sources.lock.yaml` (see README's *`video-as-code fetch`*). |
| `url:` | `{id: music, url: [https://www.youtube.com/watch?v=..., https://drive.google.com/file/d/.../view]}` | One link or a list, tried in order until one works: YouTube (audio only, via `yt-dlp`), Google Drive (shared as "anyone with the link"), or any direct link. Stored as `<dir>/<id><ext>`. |
| `derive:` | `{id: jump, derive: {source: VID_1.mp4, trim: [12, 16]}}` | Generated with ffmpeg from another file (below). |
| `type: title` | `{id: card, type: title, text: "Hello"}` | A text card generated by the tool (below). |

Other keys every entry may have:

| Key | Default | Meaning |
|---|---|---|
| `type` | from the extension | `video`, `audio`, `image`, `animation` or `title`. Only needed to override the guess, or for titles. |
| `folder` | - | Bin folder to show it in when the project is opened in kdenlive. |
| `local_name` | - | With `source:`/`url:` only: the local file name to use instead of the automatic one (same extension as the original). |

### `derive:`

Exactly one input:

| Key | Meaning |
|---|---|
| `source` | An album item, by original file name - or `{name, taken, size, id}` like a bin `source:` (downloaded by `fetch`; if a plain `source:` clip uses the same item, its file is shared). |
| `path` | A local file. |
| `clip` | Another bin entry's file, by `id` (it may itself be derived). |

Then any of these operations, applied in this order:

| Key | Example | Meaning |
|---|---|---|
| `motion` | `true` | Take the short video inside a phone's motion photo (`.MP.jpg`) instead of the photo; the result is an `.mp4`. |
| `rotate` | `180` | Turn the picture 90, 180 or 270 degrees clockwise, before anything else (so `crop` works on the turned picture): for a photo whose EXIF orientation tag is wrong, shown upside down or on its side. |
| `trim` | `[12, 16]` | Keep only these input seconds `[start, end)`. |
| `cut` | `[[18.44, 20.36]]` | Remove these input-second ranges from the middle, picture and sound alike. |
| `crop` | `{x: 0, y: 156, w: 4080, h: 2760}` | Keep this rectangle, in input pixels. |
| `scale` | `[1920, 1080]` | Resize; `-1` on one side keeps the aspect ratio. Two sizes of another shape stretch the picture to it (square pixels: it shows at that shape). |
| `devignette` | `0.81` | Undo a vignette baked into the input (a shot taken from an old edit, say): how bright its corners are relative to its centre, between 0 and 1. |
| `fit` | `{size: [1440, 960], background: blur}` | Make the whole picture fit inside `size`, centred, and fill the rest: `blur` (the default) with a blurred copy of the picture enlarged to cover it, `black` with black. Fits a portrait photo into a landscape frame without cropping it. With `blur`, `lighten: 0.35` (0-1) brightens the blurred copy - every channel times 1 + 5 x `lighten`, so its colours stay (a dark brown turns light brown, not grey) - for a dark picture (a cave, a night shot) whose blurred surround would otherwise look like plain black. As a **card** on its blurred copy (`blur` only): `margin: 60` keeps at least that many px of the copy all round the picture, `radius: 40` rounds its corners, `edge: 8` softens its edge over that many px, `shadow: 0.45` (0-1) lays a soft shadow of its shape under it, `dim: 0.2` (0-1) darkens the copy so the card stands out - e.g. `{size: [1920, 1080], background: blur, margin: 60, radius: 40, edge: 8, shadow: 0.45, dim: 0.2}`; any picture shape, a portrait one as a tall card. `margin`, `radius` and `edge` are whole pixels, `margin` less than half the smaller side of `size`; `shadow` and `dim` go from 0 up to (not including) 1; `dim` and `lighten` don't go together. On a video, the card's shape and shadow are worked out once, on its first frame. |
| `feather` | `16` | Soft edges: the picture fades out over its last that many pixels on every side, to black (and, with `fit: blur`, the picture itself also melts into its blurred surround). |
| `vignette` | `0.81` | Darken the edges, the reverse of `devignette`: how bright the corners end up relative to the centre, between 0 and 1. |
| `fps` | `30` | Re-time to this frame rate. |
| `reverse` | `true` | Play backwards, picture and sound. |
| `volume` | `1.41` | Multiply the sound's level. |
| `intra` | `true` | Encode a video with every frame a keyframe: avoids `speed:` stalling near the end of a file with long keyframe intervals, at the cost of a bigger file. |
| `upscale` | `4` | Enlarge a still or a gif x4 with Real-ESRGAN (slow; `fetch` offers to install it). |
| `openshot` | `[{effect: Wave, amplitude: 0.6}]` | libopenshot's effects, last of all, in order: video ones (`Wave`, `AnalogTape`, `LensFlare`, `SphericalProjection`, `Pixelate`, `ColorShift`, `Shift`, `Bars`...) and audio ones (`Echo`, `Robotization`, `Whisperization`, `Distortion`...). Each a map with its `effect` and any of its properties: a number, a map of seconds to numbers (keyframes), a colour (`[r, g, b]` or `"#rrggbb"`), `true`/`false` or text - checked against libopenshot's own catalog. `Caption` (draws nothing outside OpenShot's editor: use a `type: title` clip) and `Mask` (needs a mask picture it can't be given) are refused. Needs libopenshot's Python bindings (`python3-openshot`, which `openshot-qt` brings): run with the system's Python (`VIDEO_AS_CODE_OPENSHOT_PYTHON` for another). Not with `upscale`. |

A gif stays a gif, a still becomes `.png`, audio `.m4a` and video `.mp4`.
Outputs are cached in `<dir>/.derived/` and regenerated only when the spec
or the input changes.

### Lottie animations (`type: animation`)

A `.json` Lottie animation (LottieFiles' format) or a `.tgs` one
(Telegram's, gzipped) is a bin clip like a video: played by melt's
`glaxnimate` module - kdenlive's "Animation" clips - with its own
transparency, so on a track above it plays over what's below; as long
as its own frames say (`ip` to `op` at `fr` per second). The
distributions' melt has the module; one without it is an error that
says so.

### Title cards (`type: title`)

| Key | Default | Meaning |
|---|---|---|
| `text` | *(required)* | The text; may span several lines. |
| `font` | `Sans` | Font family. |
| `font_size` | `48` | Size in pixels. |
| `color` | `[255, 255, 255, 255]` | Text colour. |
| `background` | `[0, 0, 0, 0]` | Background colour (transparent by default). |
| `font_weight` | `400` | Weight, from `100` to `900` (`400` normal, `700` bold) or a name: `thin`, `light`, `normal`, `medium`, `semibold`, `bold`, `black` - the font needs a face of about that weight. |
| `italic` | `false` | Italic. |
| `outline` | `0` | An outline around each letter, this many pixels wide. |
| `outline_color` | `[0, 0, 0, 255]` | Its colour. |
| `shadow` | `false` | A drop shadow: `true` (soft, dark, 3 px down and right), or `{color: [r, g, b, a], blur: 6, offset: [3, 3]}`. |

`font` takes any font installed on the machine that renders (`fc-list`
lists them, e.g. `"DejaVu Serif"`, `"Ubuntu"`); one it doesn't have is
replaced by a default one without warning.

## `timeline.tracks:`

`timeline:` holds one key, `tracks`: a list of tracks. Later tracks are stacked above earlier ones: a clip
with `fade_in` on a later track fades in over what plays below it.

| Key | Default | Meaning |
|---|---|---|
| `type` | *(required)* | `video` or `audio`. |
| `name` | `V<n>` / `A<n>` | Track name (`transitions:` refer to tracks by it). |
| `clips` | `[]` | The clips placed on it (below). Clips on one track can't overlap. |
| `pan` | - | Audio tracks only: `-1.0` (left) to `1.0` (right). |
| `hidden` | `false` | Hide the track's picture. |
| `locked` | `false` | Lock the track (when opened in kdenlive). |
| `audio_muted` | `false` | Mute the whole track's sound. |

### Clips on a track

Each is `{clip: <bin id>, start: <seconds>, ...}`. `clip` and `start` are
required.

**Which part of the source, and for how long**

| Key | Meaning |
|---|---|
| `start` | Where on the timeline it starts. |
| `in` | Where in the clip to start (default `0`); with `speed`, in timeline (already sped-up) seconds. |
| `out` | Where in the clip to stop, same units as `in`. |
| `duration` | How long it lasts on the timeline (instead of `out`). |
| `source_in`, `source_out` | Start and end in *real source seconds*, whatever `speed` is. Both or neither, and not together with `in`/`out`/`duration`. With `speed_ramp`, `source_in` alone sets where the ramp starts reading. |

**Speed**

| Key | Default | Meaning |
|---|---|---|
| `speed` | - | Constant speed factor (`2.0` = twice as fast, negative = backwards). |
| `preserve_pitch` | `false` | Keep the sound's pitch when `speed` changes it. |
| `speed_ramp` | - | Changing speed: `[{at: 0, speed: 4}, {at: 0.75, speed: 1}]` - each entry starts a constant-speed stretch at `at` seconds into the clip; the first must be at `0`. Not together with `speed` or `in`; needs `out` or `duration`. |
| `speed_ramp_image_mode` | `nearest` | `nearest` (repeat frames) or `blend` (mix neighbouring frames) when slowed down. |
| `boomerang` | `false` | Play the stretch forward and then backward, as two placements (the backward one muted, starting with a hard cut). Needs `out`, `duration` or `source_in`+`source_out`; not with `speed_ramp`. |
| `boomerang_trim_hold` | `true` | With `boomerang`: if the source holds its last frame (a gif's long last delay), shorten both halves so the turnaround doesn't stall. |
| `freeze_at` | - | Freeze the picture at this second of the clip (counted from its first visible frame). Alone, the whole clip shows that frame. |
| `freeze_before` / `freeze_after` | `false` | With `freeze_at`: play normally on one side and freeze only before / after it. |

**Fades and sound**

| Key | Meaning |
|---|---|
| `fade_in`, `fade_out` | Fade length. On video, from/to transparent (so it crossfades with the track below, or black if there is none); on audio, from/to silence. |
| `fade_in_level`, `fade_out_level` | Audio: start the fade-in at / end the fade-out at this level (`0`-`1`) instead of silence. |
| `gain` | Audio level multiplier (`2.0` = twice as loud). |
| `mute` | `true` silences the clip's own sound. |

**Framing**

| Key | Meaning |
|---|---|
| `crop` | Margins removed from the picture - `{top: 0, bottom: 316}`: any of `top`, `right`, `bottom`, `left`, in pixels of the source file - and the rest scaled to fill the frame. Not together with `position`/`size`/`position_keyframes`. |
| `position`, `size` | Picture-in-picture: `[x, y]` and `[w, h]` in canvas pixels. The picture may run past the canvas's edges (only what's on it shows), not lie wholly outside it. |
| `position_keyframes` | Animated picture-in-picture: `[{at: 0, position: [x, y], size: [w, h]}, ...]`, first at `0`. Each keyframe, like `position`/`size`, may run past the canvas's edges - a full-frame card sliding in from above starts at a negative `y`. Not together with `position`/`size`. |
| `ken_burns_from`, `ken_burns_to` | Pan and zoom from one rectangle to another over the clip: `[x%, y%, w%, h%]` of the frame. Both or neither. |

**Effects**

| Key | Default | Meaning |
|---|---|---|
| `color_grade` | - | Three-way colour grading: any of `lift` (shadows), `gamma` (midtones), `gain` (highlights), each `[r, g, b]`. Neutral is lift `[0,0,0]`, gamma and gain `[1,1,1]`; e.g. `{gamma: [1.2, 1.2, 1.2]}` brightens the midtones. |
| `color_grade_keyframes` | - | A second grade on top of `color_grade`, animated: `[{at: 0, lift: [0, 0, 0]}, {at: 1.5, lift: [0.2, 0.18, 0.06]}]`, `at` in seconds into the clip (the first `0`, each later than the one before), every keyframe with the same groups; eased linearly from one to the next and held after the last. For a swell of light that rises and falls across clips, give each clip its stretch of it. |
| `old_film`, `film_dust`, `film_scratches`, `film_grain` | - | `true` turns on each "old film" filter (flicker and uneven developing, dust, scratches, grain). |
| `film_grain` as a map | `{noise: 12}` | Instead of `true`: grain that changes every frame, `noise` its strength (default `40`), with `contrast` and `brightness` in percent, `100` (the default) meaning unchanged. `true` keeps kdenlive's own settings (`40`/`160`/`70`), which also harden and darken the whole picture. |
| `film_dust` as a map | `{style: dots, size: 4, count: 10}` | Instead of `true`: `style: svg` (the default) draws MLT's dust pictures, `size` percent of the clip's width across (default `2`); `dots` draws small round specks, darker and lighter, at most `size` pixels in radius (default `4`), like the dust on an old print. `count`: at most that many per frame (default `10`). One style per project: `dots` makes `render` run melt on a copy of its data folder without the dust pictures (opened in kdenlive, the project shows them). `pictures` (with `svg`): the pictures to pick from instead of MLT's five - theirs by name (`dust1`, `dust2` blots, the latter drawn white; `dust3`-`dust5` hairs) or `.svg` files of the project, a repeated one coming up more often; e.g. `[dust3, dust4, dust5, assets/speck.svg]`, the speck drawn small inside a larger transparent picture. One set per project, also by swapping the data folder. `hold`: the same specks for that many frames running (default `1`, new ones every frame). |
| `stabilize` | - | `true` smooths out camera shake (single pass). |
| `lut` | - | A 3D LUT file applied to the picture, after `color_grade` - `.cube`, `.3dl`, `.dat` or `.m3d`, relative to the project file: a look, or a camera's log-to-Rec.709 conversion. Or a LUT's name, without extension (`lut: teal-orange`): looked up in `luts/` next to the project file, then in `~/.local/share/video-as-code/luts/` (`$XDG_DATA_HOME`) - a pack of looks shared by every project. |
| `lut_interp` | `tetrahedral` | How the LUT is interpolated between its points: `nearest`, `trilinear` or `tetrahedral`. |
| `glsl` | - | A GLSL effect on the picture, run on the GPU: one of this project's shaders by name (`crt`, `chromatic`, `wave`, `glitch`, `kaleidoscope`, `vhs`) or a `.glsl` file of your own, relative to the project file; `{shader: ..., params: {...}}` to set its parameters. See *`glsl:` shaders* below. |
| `focus_rect` | - | `[x%, y%, w%, h%]` kept sharp while the rest is blurred. |
| `focus_blur` | `4.0` | Blur radius for `focus_rect`, percent of the width (`0`-`50`). |
| `chroma_key` | - | Colour to make transparent (`"#00ff00"`). |
| `chroma_key_distance` | `0.35` | How far from that colour still counts (`0`-`1`). |
| `reveal` | - | How the clip appears: `iris`, `flip`, `glitch`, `delaygrab` or `cairogradient`. |
| `overlay_text` | - | Text burned onto the clip; may use MLT's keywords (`#timecode#`, `#frame#`...). |
| `overlay_font` | `Sans` | Its font. |
| `overlay_font_size` | `48` | Its size. |
| `overlay_color` | `[255, 255, 255, 255]` | Its colour. |
| `overlay_background` | `[0, 0, 0, 0]` | Its background. |
| `melt` | - | Any other MLT filter, by its service name: a list of `<service>: {<property>: <value>}` (see *`melt:` and `melt/<service>`* below). |

## `scenes:`

The high-level form: each scene is a list of clips that play one after
another, with their timing worked out. Scenes follow each other, starting
at second 0.

| Key | Default | Meaning |
|---|---|---|
| `scene` | `scene<n>` | Name. |
| `clips` | *(required)* | The clips (below). |
| `pacing` | `normal` | How long each clip lasts when `duration` isn't given: `energetic` (stills 1.5 s, videos up to 3 s), `normal` (3 s / up to 6 s) or `slow` (5 s / up to 10 s). A video shorter than its cap plays whole. |
| `duration` | - | Total length of the scene, split evenly across its clips. |
| `transition` | `none` | Between consecutive clips: `none`, `dissolve`, `wipe`, `affine`, `iris`, `flip`, `glitch`, `delaygrab` or `cairogradient` - all but `none`/`dissolve` need video - `gl` (one of gl-transitions', below) or any MLT transition as `melt/<service>` (below). |
| `transition_duration` | `1.0` | Length of each transition. |
| `luma` | `linear_x` | With `wipe`: its shape - `linear_x`, `linear_y`, `diagonal`, `radial`, `square`, `clock`, `spiral`, `bars_horizontal`, `bars_vertical`, `checkerboard`, `heart` or `bowtie`; or, with openshot-qt installed, one of OpenShot's 412 as `openshot/<name>` (see the README's *OpenShot's transitions*). |
| `luma_softness` | `0` | With `wipe`: how soft the edge between the two clips is, from `0` (hard) to `1`. |
| `luma_reverse` | `false` | With `wipe`: the shape's light parts change first instead of its dark ones. |
| `style` | `zoom` | With `affine`: `zoom` or `slide`. |
| `transition_properties` | - | With `melt/<service>`: its properties, `{<property>: <value>}`. |
| `gl` | - | With `gl`: which of gl-transitions' 125 (`cube`, `doorway`, `ripple`...; see *`transition: gl`* below). |
| `gl_params` | - | With `gl`: its parameters, `{<name>: <value>}`; the rest keep their defaults. |

A scene's clip is a file path (`photos/beach.jpg`, added to the bin by
itself), `{path: ...}`, or `{clip: <bin id>}`; the two map forms may also
carry `mute`, `speed` (with `preserve_pitch`), `position`, `size`,
`chroma_key`, `chroma_key_distance`, `lut` (with `lut_interp`) and `melt`,
as on a timeline clip.

## `guides:`

Timeline markers: `[{at: 12.5, text: "Chorus", category: 0}, ...]`.

| Key | Default | Meaning |
|---|---|---|
| `at` | *(required)* | Where, in seconds. |
| `text` | `Marker` | Its label. |
| `category` | `0` | kdenlive's marker category (its colour). |

## `transitions:`

A transition between two tracks over a time window, written directly
(`scenes:` produces these by itself; `import` writes them too):

| Key | Default | Meaning |
|---|---|---|
| `type` | *(required)* | `wipe`, `affine`, `gl` or `melt/<service>`. |
| `track_a`, `track_b` | *(required)* | The two tracks, by `name`. |
| `start`, `end` | *(required)* | The window, in timeline seconds. |
| `luma` | `linear_x` | With `wipe`: its shape (see `scenes:`). |
| `softness` | `0` | With `wipe`: as a scene's `luma_softness`. |
| `reverse` | `false` | With `wipe`: as a scene's `luma_reverse`. |
| `style` | `zoom` | With `affine`: `zoom` or `slide`. |
| `properties` | - | With `melt/<service>`: its properties, `{<property>: <value>}`. |
| `gl`, `gl_params` | - | With `gl`: as on a scene. |

## `melt:` and `melt/<service>`

For what there's no key of its own for: any filter or transition the
`melt` that renders the project has (`melt -query filters`,
`melt -query transitions` list them - frei0r's ~100, ffmpeg's as
`avfilter.*`, MLT's own), by its service name.

```yaml
timeline:
  tracks:
    - type: video
      clips:
        - clip: beach
          start: 0
          duration: 4
          melt:
            - frei0r.cartoon: {diffspace: 0.4}
            - avfilter.hue: {av.h: "0=0;100=180"}   # keyframes, MLT's own syntax
            - frei0r.invert0r                       # no properties
scenes:
  - transition: melt/composite
    transition_properties: {geometry: "0=50%/50%:0%x0%;-1=0/0:100%x100%"}
    clips: [a.jpg, b.jpg]
```

Each `melt:` entry becomes one filter on the clip, after those of the
clip's own keys, in the order given; a `melt/<service>` transition goes
between the scene's two alternating tracks over each overlap (on
`transitions:`, between `track_a` and `track_b` over `start`-`end`),
with no fade on either clip, as `wipe` does. Values are written as they
are: MLT's own units, ranges (frei0r's parameters go from 0 to 1) and
keyframe syntax. A frei0r parameter may be named by its number or by its
name (`melt -query filter=frei0r.cartoon` shows both).

When there's a `melt` to ask, an unknown service or property is an error
(with the closest names) instead of a render that silently does nothing;
a service melt doesn't describe takes any property. Opened in kdenlive,
a filter it has no description of renders but may not show in its
effect stack. `import` brings any filter over the whole clip and any
transition between two tracks that the installed melt has, and that it
doesn't otherwise recognize, back as these entries.

## `transition: gl`

The 125 transitions of [gl-transitions](https://gl-transitions.com) - a
cube turning, doors opening, a page curling, a morph, ripples, a burn... -
each a GLSL shader run on the GPU:

```yaml
scenes:
  - transition: gl
    gl: cube
    gl_params: {persp: 0.5, reflection: 0.2}   # optional
    clips: [a.jpg, b.jpg]
```

`video-as-code gallery` shows every one playing, with its name; each
one's parameters, their types (`float`, `bool`, `vec2`/`vec3`/`vec4` as
lists of numbers, `ivec2` as whole numbers) and ranges are in
`src/video_as_code/gltransitions_data/catalog.json`, and a wrong one is
an error that says what it takes.

They need a small plugin built once: `video-as-code install-gl-transitions`
(a C compiler and the EGL, GLES and frei0r headers - `libegl-dev`,
`libgles-dev`, `frei0r-plugins-dev` on Debian/Ubuntu). In the .kdenlive
they're a `frei0r.gltransition` transition, which kdenlive renders as
long as its melt finds the plugin (`FREI0R_PATH`). A machine with no GPU
renders them on the CPU (Mesa's llvmpipe), more slowly.

## `presets:`, `presets_file:` and `use:`

Named sets of keys, written once and used wherever a project file has a
map - a scene, a clip (on a track or in a scene), a bin entry, a
`transitions:` entry:

```yaml
presets:
  page: {transition: gl, gl: BookFlip, transition_duration: 0.8}
  warm: {lut: looks/warm.cube, film_grain: {noise: 12}}
  warm-page: {use: [page, warm]}          # a preset may use others

scenes:
  - use: page
    clips: [a.jpg, {path: b.jpg, use: warm}]
  - use: page
    transition_duration: 1.5               # written here: wins over the preset's
    clips: [c.jpg, d.jpg]
```

| Key | Where | Meaning |
|---|---|---|
| `presets` | top level | A map of names to maps of keys. |
| `use` | any map | A preset's name, or a list of them: their keys go in first, in that order, and the keys written next to `use:` last, so those win. A value replaces the preset's whole (a map isn't merged into the preset's map). |
| `presets_file` | top level | A YAML file, relative to the project file, with one key, `presets:`; its presets come first, and the project's own `presets:` of the same name replace them - for presets shared by several projects. |

`use:` is expanded before anything else reads the file, so it works
with every key. A name that isn't a preset, or presets that use each
other in a circle, is an error that says where. Relative paths inside a
preset (a `lut:`, a `path:`) are relative to the project file that uses
it, like the rest of that file's: a presets file shared by projects in
other folders takes absolute paths.

```yaml
# presets.yaml, next to several projects' files
presets:
  page: {transition: gl, gl: BookFlip}
  title-style: {font: "DejaVu Serif", font_weight: bold, shadow: true}
```

```yaml
# a project
project: {...}
presets_file: presets.yaml
bin:
  - {id: card, type: title, text: "Day one", use: title-style}
```

## `parts:` - a project in several files

The main file has `project:` and, instead of `bin:`/`scenes:`/`timeline:`,
a list of parts, each a file of its own; a part may list parts in turn:

```yaml
# trip.yaml
project: {id: trip, title: Trip, fps: 25, width: 1920, height: 1080}
presets_file: presets.yaml
parts:
  - intro.yaml
  - beach.yaml
  - {file: mountains/all.yaml, transition: gl, gl: cube, transition_duration: 1.2}
```

```yaml
# beach.yaml - a part: no project:, the rest as in any project file
bin:
  - {id: wave, path: media/wave.mp4}
scenes:
  - use: page
    clips: [{clip: wave}, media/sand.jpg]
```

| Key | Where | Meaning |
|---|---|---|
| `parts` | the main file, or a part | The parts, played one after the other: a file name (relative to the file listing it), or `{file: ..., ...}` with the keys of the transition into that part. A file with `parts:` has no `bin:`, `scenes:`, `timeline:`, `transitions:` or `guides:` of its own. |
| `file` | a `parts:` entry | The part's file. |
| `transition` and its keys | a `parts:` entry | From the part before into this one: `dissolve`, `wipe` (with `luma`, `luma_softness`, `luma_reverse`), `affine` (`style`), `iris`, `flip`, `gl` (`gl`, `gl_params`) or `melt/<service>` (`transition_properties`), over `transition_duration` (default 1). The parts overlap by it; none, a cut. glitch, delaygrab and cairogradient (bursts on a clip) don't go between parts. |

- Each part is a sequence of its own in the `.kdenlive` - kdenlive's
  nested sequences, named after the part's file - placed in the one that
  lists it; the main file's is the timeline kdenlive opens.
- A part has no `project:`: it takes the main file's. Its relative paths
  are its own folder's, so a part can be moved with its media.
- A part's bin ids are its own: two parts may both have a clip `a`.
- Presets: the main file's (and its `presets_file:`) are in force in
  every part; a part's own add to them for itself and the parts it lists.
- A part that lists itself, through any number of others, is an error
  that shows the chain.
- `--part <path>` (on `build`, `render`, `validate`) takes one part
  alone: its file's path from the main file's folder, without `.yaml`
  (`--part mountains/all`); the output is named after both
  (`trip-mountains-all.mp4`).
- `fetch` (and `--fetch`) goes through the parts one by one, each in its
  own folder - its own `sources.lock.yaml`, its own downloads - and a fix
  asked for in the terminal is written into the part's file, where the
  clip is. Old files are pruned before a render in each part's folder;
  not with `--part`, whose render doesn't read the other parts' files.
- `--proxy` makes each part's light copies in that part's own `.proxies/`;
  `--split` cuts segments on the whole timeline, a part's clips counted
  where the part plays.
- `import` of a `.kdenlive` whose timeline is parts in turn (the shape a
  build gives it: sequences placed whole, each once, one after the other
  or overlapping by a transition it knows) writes the main file with
  `parts:` and a file per part, named after its sequence and next to the
  main one, its paths absolute. Nested sequences in any other shape stay
  in `x_kdenlive_raw`.

## `glsl:` shaders

`glsl:` on a clip runs a GLSL ES 3.00 shader over its picture on the GPU,
through a small plugin built once with `video-as-code install-gl-transitions`
(the one `transition: gl` uses has this sibling).

| Shader | Parameters (default, range) | What it does |
|---|---|---|
| `crt` | `curvature` (0.15, 0-0.6), `scanlines` (0.35, 0-1), `vignette` (0.4, 0-1) | An old CRT screen: a curved picture, scanlines, darker corners. |
| `chromatic` | `amount` (0.012, 0-0.1) | Red and blue pulled apart toward the edges. |
| `wave` | `amplitude` (0.012, 0-0.1), `frequency` (14, 1-80), `speed` (2, 0-10) | The picture ripples, as through moving water. |
| `glitch` | `intensity` (0.5, 0-1), `rate` (6, 0.5-30 per second) | Bands jump sideways and split their colours, now and then. |
| `kaleidoscope` | `segments` (6, 2-24), `turn` (0.1, -2-2 turns per second) | Mirrored segments around the centre, turning. |
| `vhs` | `strength` (0.6, 0-1) | A worn tape: colour bleeding, a rolling bright band, noise, jitter. |

A shader of your own is a `.glsl` file with `vec4 effect(vec2 uv)`: it
reads the picture with `getColor(uv)` (uv from 0 to 1, origin bottom
left) and has `time` (seconds into the clip), `progress` (0 to 1 over
it), `ratio` (width / height) and `resolution` (in pixels); its own
`uniform`s get the values in `params` (numbers, `true`/`false`, lists of
2-4 numbers for `vec2`-`vec4`) - one not given is 0.

```glsl
// warm.glsl
uniform float amount;
vec4 effect(vec2 uv) {
  vec4 c = getColor(uv);
  return vec4(c.r + amount, c.g, c.b - amount, 1.0);
}
```

```yaml
- {clip: beach, start: 0, duration: 4, glsl: {shader: looks/warm.glsl, params: {amount: 0.1}}}
```
