# How to make a video

The workflow: write a YAML file and render it straight to video. Repeat
as you tweak the YAML - that's the whole point of versioning a video as a
YAML file instead of a binary project. If you'd rather finish it by hand,
build a `.kdenlive` from it and open that in kdenlive.

## 1. Write the YAML

Two forms, pick whichever fits what you're doing:

- **`timeline.tracks:`** (low-level): you place each clip explicitly with
  `start:`/`duration:`/`fade_in:`/etc. Full control over every field this
  tool supports. See `examples/simple/project.yaml`.
- **`scenes:`** (high-level): a scene is just a list of clips that play
  one after another, with timing computed for you (`pacing`, or an
  explicit `duration` split across the clips), plus transitions
  (`dissolve`/`wipe`/`affine`/`iris`/`flip`/`glitch`/`delaygrab`/
  `cairogradient`). See `examples/scenes/project.yaml`.

Every key either form accepts is in `docs/project-yaml.md`.

Copy whichever example is closer to what you need and start editing -
that's faster than writing one from scratch. Both forms can be mixed in
the same project (a `scenes:` block compiles down into `timeline.tracks:`
before anything else runs).

If the material lives in a Google Photos album, bin clips can name album
items by their original file name (`source:`) instead of a local
`path:`, and trims/crops/reversals of them can be described with
`derive:` instead of kept as hand-made files; what isn't in the album
(the music, say) can name links to download it from (`url:`). Then,
before validating:

```bash
video-as-code fetch project.yaml
```

Downloads the album items that aren't on disk yet (as `<capture
date>_<id><ext>`) and the `url:` clips, and generates the `derive:` clips. Only needed when
something new comes from the album; `build`/`render --fetch` do the same
first. See `README.md`'s *`video-as-code fetch`* section.

## 2. Validate

```bash
video-as-code validate project.yaml
```

Checks the YAML's shape, that every referenced asset exists on disk, that
`in`/`out`/`duration` are consistent with each source file's real
duration, and that clips don't overlap within a track - without writing
anything. Catches typos before you burn time on a build/render cycle.

## 3. Render

```bash
video-as-code render project.yaml -o output.mp4
```

Needs `melt` (the MLT engine kdenlive itself renders with) on `PATH`.
Equivalent to `build` followed by `melt project.kdenlive -consumer
avformat:output.mp4`, minus the intermediate file - use `-k`/
`--keep-kdenlive` if you want to keep it anyway.

## 4. Open it in kdenlive (optional)

```bash
video-as-code build project.yaml -o project.kdenlive
```

Generates the `.kdenlive` project file. Open it in kdenlive to inspect,
edit further by hand, or render from the GUI.

The `.kdenlive` is a build artifact, not something to commit - it's
regenerated deterministically from the YAML every time (see the
`.gitignore` this repo ships with).

For OpenShot instead: `video-as-code build project.yaml --format openshot`
gives a `.osp` - see `README.md`'s *An OpenShot project*.

### What you see in kdenlive

Most of a project opens in kdenlive as if it had been made there: clips,
tracks, fades, transitions, its own effects. A few things go beyond what
kdenlive itself offers, and show differently:

- **`derive:` clips - libopenshot's effects (`openshot:`) among them -
  are files.** They're generated before the build, into
  `<dir>/.derived/`, and the `.kdenlive` points at those files: a Wave or
  an AnalogTape comes in already applied to the clip's picture, not as an
  effect in its effect stack. It plays without OpenShot installed, but it
  can't be tweaked or removed there - change the YAML and build again.
  Moving the `.kdenlive` to another machine means taking `.derived/` with
  it.
- **`transition: gl` and `glsl:`** need the plugin
  `video-as-code install-gl-transitions` builds: kdenlive renders them as
  long as its melt finds it (`FREI0R_PATH`).
- **`melt:` filters kdenlive has no description of** render, but may not
  show in its effect stack.
- **`film_dust` with `style: dots`**: `render` draws round specks, but
  kdenlive shows MLT's dust pictures instead.

## 5. Iterate

Change the YAML, re-run `validate`/`build`/`render` - or leave
`video-as-code watch project.yaml` running: each save renders a light
preview again (only what changed) and plays it from there. To look at
one moment at full quality, `render --from 1:20 --to 1:35` renders just
that stretch. Since the YAML is
what you check into version control, `git diff` on it is a real, readable
diff of what changed in the edit - trims, timing, added clips - the same
way it would be for code.

## Reference

- Full command reference and field list: `README.md`.
- What's implemented and how each mechanism was verified:
  `ROADMAP.md`.
- Already have a `.kdenlive` and want the YAML back instead?
  `video-as-code import project.kdenlive -o project.yaml` - see
  `README.md`'s *Usage* section. Have a rendered video with no project
  file at all instead? See `docs/reconstructing.md`.
