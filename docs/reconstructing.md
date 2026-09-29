# How to recover a YAML from an already-rendered video

For when you have a flat, already-rendered video (a `.mp4`, no edit
metadata) and the source assets that probably went into it, but no
project file at all - not even a `.kdenlive`. `video-as-code
reconstruct` recovers an approximate YAML by comparing pixel/audio
content, not by parsing structure (there's no structure to parse).

If you *do* have a `.kdenlive` project file, use `video-as-code
import` instead (see `README.md`) - it's exact, since it's reading real
edit data rather than guessing from pixels.

## 1. Install the `reconstruct` extra

```bash
pip install -e ".[dev,reconstruct]"
```

Needs `numpy`/`Pillow` on top of the base install, plus `ffmpeg`/
`ffprobe` on `PATH`.

## 2. Gather the candidate source assets

Put every file that might appear in the render into one folder - photos,
clips, audio tracks. Extra, unused files in there are fine (they're
just candidates); the matcher figures out which ones actually appear.

## 3. Run it

```bash
video-as-code reconstruct rendered.mp4 assets/ -o output.yaml
```

This is a real, multi-minute pipeline on non-trivial footage (several
full-video decodes) - it prints progress per stage as it runs (probing,
scanning the asset bank, extracting signatures, matching each detected
segment) so it doesn't look hung.

## 4. Read the output

- Confidently matched clips land in `output.yaml`'s `timeline.tracks:`
  like any other project - trims, fades, and speed changes recovered
  where detected.
- Anything the matcher *isn't* confident about is never silently
  guessed: it's collected under `x_reconstruct_unmatched:` instead, each
  entry with a `where` (timestamp range), a `best_guess`, and a
  `confidence_pct` (0-100, higher is better - see `ROADMAP.md` for what
  it means). The same detail is printed as a warning while it runs.

## 5. Review and fix up by hand

Treat `output.yaml` as a rough first draft, not a finished project:

- Check every `x_reconstruct_unmatched` entry against the real video at
  that timestamp - add the missing `clip:`/`start:`/`duration:` by hand
  once you know what actually belongs there.
- Known blind spot: matching compares against each asset's *plain*
  content, so a segment that's mid-fade in the render can score too low
  to match confidently, or get split into extra spurious segments - see
  `ROADMAP.md` for the details.

## 6. Verify

```bash
video-as-code render output.yaml -o check.mp4
```

Build and render the recovered YAML, then compare `check.mp4` against
the original `rendered.mp4` - confirms the YAML actually reproduces what
you think it does before you rely on it. From here on it's a normal
YAML project - see `docs/making-a-video.md`.
