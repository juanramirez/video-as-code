# How to make a video

The workflow: write a YAML file, build a `.kdenlive` from it, then either
open that in kdenlive or render it straight to video. Repeat as you tweak
the YAML - that's the whole point of versioning a video as a YAML file
instead of a binary project.

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

## 3. Build

```bash
video-as-code build project.yaml -o project.kdenlive
```

Generates the `.kdenlive` project file. Open it in kdenlive to inspect,
edit further by hand, or render from the GUI.

The `.kdenlive` is a build artifact, not something to commit - it's
regenerated deterministically from the YAML every time (see the
`.gitignore` this repo ships with).

## 4. Render straight to video (optional)

```bash
video-as-code render project.yaml -o output.mp4
```

Needs `melt` (the MLT engine kdenlive itself renders with) on `PATH`.
Equivalent to `build` followed by `melt project.kdenlive -consumer
avformat:output.mp4`, minus the intermediate file - use `-k`/
`--keep-kdenlive` if you want to keep it anyway.

## 5. Iterate

Change the YAML, re-run `validate`/`build`/`render`. Since the YAML is
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
