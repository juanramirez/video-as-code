# kdenlive-generator

Programmatic generator for [kdenlive](https://kdenlive.org) projects from a
declarative YAML description, meant for versioning videos with a VCS (like
Git): the `.yaml` is what you check in; the `.kdenlive` (XML/MLT) is regenerated
deterministically with `kdgen build` and doesn't need to live in the repo.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
kdgen build project.yaml [-o output.kdenlive]   # generate the .kdenlive
kdgen validate project.yaml                     # validate without building
kdgen import project.kdenlive [-o output.yaml]  # existing .kdenlive -> YAML
```

`kdgen import` recognizes everything this generator knows how to produce
(video/audio tracks, trimmed clips, fade to/from black, volume fades,
simple `qtblend` picture-in-picture compositing). Anything it doesn't
recognize isn't discarded: it's kept as-is under `x_kdenlive_raw:` in the
output YAML so you can decide by hand what to do with it.

This only reconstructs a YAML from an existing `.kdenlive` *project* file.
Reconstructing one from an already-rendered video plus an assets folder is a
different, much harder problem (content-based matching, not XML parsing) —
see `BACKLOG.md`.

See `examples/simple/` for a minimal working project (tiny generated
assets, one audio + one video track, fades and trims):
`kdgen build examples/simple/project.yaml`.

### Language

All user-facing text (validation errors, CLI output, import warnings) goes
through a small message catalog (`i18n.py`), keyed by token and looked up at
call time. English is the source of truth and the fallback for any locale
or key that isn't translated. Set `KDGEN_LANG=es` to get the Spanish
catalog:

```bash
KDGEN_LANG=es kdgen validate project.yaml
```

We deliberately don't read the system `LANG`/`LC_ALL`: locale selection is
opt-in and explicit, so behavior doesn't silently change across machines
and test runs stay deterministic. Code comments, docstrings, and this
README are always in English regardless of `KDGEN_LANG` — that setting only
affects what a `kdgen` *user* sees at runtime.

## Current status

Verified by the test suite (`pytest`, 12 tests, all passing):

- **YAML schema and validation** (`schema.py`): checks shape, types, that
  referenced assets exist on disk, that each clip's `in`/`out`/`duration`
  are consistent with the source file's real duration (via `ffprobe`,
  `probe.py`), and that clips don't accidentally overlap within a track.
  Collects all errors before raising, instead of stopping at the first one.
- **`.kdenlive` generation** (`xmlgen.py`): replicates the structure that
  kdenlive 24-26.x produces on save (per-track tractors, dual playlist for
  kdenlive's "mix" feature, project bin, fade to/from black on video,
  volume fades on audio, default `qtblend` filter). Deterministic output
  (IDs derived via `ids.py`, no hashes/thumbnails, which kdenlive
  recomputes on open anyway).
- **Reverse importer** (`reverse.py`): `.kdenlive` -> YAML, with a clean
  split between what's recognized and what isn't (`x_kdenlive_raw`).
- **CLI** (`cli.py`): `build` / `validate` / `import`.
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
