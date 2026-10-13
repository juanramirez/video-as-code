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
video-as-code render project.yaml [-o output.mp4]       # build + render straight to video via melt
video-as-code fetch project.yaml [--verify]             # download source: clips from the shared album
```

`build` and `render` also take `--fetch`, to run `fetch` first.

`video-as-code render` needs `melt` (the MLT engine kdenlive itself renders with) on
PATH — it's not bundled with `video-as-code`. It's equivalent to running
`video-as-code build` and then `melt output.kdenlive -consumer avformat:output.mp4`
yourself, minus the intermediate `.kdenlive` file (built to a temp
directory and discarded).

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
whole flow.

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

Operations, applied in this order: `trim: [start, end]`, `crop: {x, y,
w, h}` (source pixels), `scale: [w, h]` (`-1` on one side keeps the aspect
ratio), `fps:`, `reverse: true` (picture and sound) and `volume:` (audio
gain factor). A gif stays a gif, a still image becomes a `.png`, audio an
`.m4a` and video an `.mp4` (h264 + aac, keeping the sound if there is any).

The result is cached under `<dir>/.derived/` with the spec's hash in its
name: `build`/`render`/`validate` generate it once, offline, whenever the
input is on disk (and again only if the spec or the input itself
changes), and `fetch` downloads album inputs and generates right away. An
album item used only as a `derive:` input is still downloaded. The cache
can always be deleted and rebuilt, so `<dir>/.derived/` belongs in your
`.gitignore` too.

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
- **CLI** (`cli.py`): `build` / `validate` / `import` / `render` / `reconstruct`.
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
