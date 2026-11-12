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

### Use your whole Google Photos library with Takeout

Start the built-in guide at any time:

```bash
video-as-code reconstruct rendered.mp4 --takeout
```

Google Takeout creates an export asynchronously; it can take hours or
days. The usual steps are:

1. Open [Google Takeout](https://takeout.google.com/) and sign in to the
   account that owns the library.
2. Choose **Deselect all**, then select **Google Photos**.
3. Choose **Next step**, keep the one-time export, then choose **Create
   export**.
4. Wait for Google's email, then download every ZIP part.
5. Put every downloaded ZIP part in one folder. Leave them compressed:
   `video-as-code` reads their file lists directly. It only decompresses a
   small number of candidate media files at a time. An already-extracted
   Takeout folder also works.

Takeout exports the whole Google Photos library, rather than a chosen date
range. Keep enough local disk space for the downloaded archives. You don't
need space for a second, fully extracted copy.

If the complete export does not fit, process its ZIP parts one at a time with
an incremental session. Download one part, run the command below, then delete
that ZIP before downloading the next. The session directory keeps only
confident matches and the best candidates for still-unmatched segments. If the
CLI reports that all video segments match, the YAML is finalized automatically.
Otherwise, mark the last part with `--takeout-final` to save the YAML with any
remaining unmatched segments flagged for review:

```bash
video-as-code reconstruct rendered.mp4 \
  --takeout-part ~/Downloads/takeout-part-001.zip \
  --takeout-session ~/Videos/rendered-takeout-session/ -o output.yaml

# Repeat for each next part, deleting the processed ZIP after the command.
# On the final part, add --takeout-final.
video-as-code reconstruct rendered.mp4 \
  --takeout-part ~/Downloads/takeout-part-00N.zip \
  --takeout-session ~/Videos/rendered-takeout-session/ \
  --takeout-final -o output.yaml
```

The same session directory and render file must be used for every part. The
session folder is small compared with the full Takeout, but keep it until the
YAML is finalized. Incremental mode supports the same optional date-priority
flags and Google Photos publishing option.

## 3. Run it

```bash
video-as-code reconstruct rendered.mp4 assets/ -o output.yaml
```

To scan the Takeout folder locally and leave the YAML pointing at local
files:

```bash
video-as-code reconstruct rendered.mp4 --takeout ~/Downloads/photos-takeout/ -o output.yaml
```

For a local YAML, only confidently matched media is extracted permanently
to a sibling folder such as `output_assets/`. Unmatched candidates are
discarded after analysis. If you know the approximate capture-date range,
give it as a priority hint; files outside it remain fallback candidates:

```bash
video-as-code reconstruct rendered.mp4 --takeout ~/Downloads/photos-takeout/ \
  --takeout-from 2020-01-01 --takeout-to 2020-12-31 -o output.yaml
```

Dates from Takeout's JSON sidecars and camera-style filenames are used to
try likely files first. Unknown dates are retained. Once every rendered
video segment has a confident match, the scan stops; otherwise it expands
to the remaining ZIP entries.

### Upload only the matched files into a new Google Photos album

This lets the matcher search the full Takeout while the finished YAML
uses remote Google Photos sources. First configure an OAuth desktop client
for the CLI (one time):

1. Open [Google Cloud Credentials](https://console.cloud.google.com/apis/credentials)
   and create or select a project.
2. Enable **Google Photos Library API** for that project.
3. Configure the OAuth consent screen and add your Google account as a test
   user if the app is in testing.
4. Create an OAuth client ID of type **Desktop app** and download its JSON.
5. Save it as `~/.config/video-as-code/photos_client.json`. The CLI also
   accepts another path through `VIDEO_AS_CODE_PHOTOS_CLIENT`.

Then run:

```bash
video-as-code reconstruct rendered.mp4 --takeout ~/Downloads/photos-takeout/ \
  --publish-to-photos -o output.yaml
```

The CLI shows the number and total size of the matched files before asking
to upload. It streams only the files used by confident matches directly
from the ZIPs into a new album it creates; it does not keep local extracted
copies of uploaded photos and videos after a successful upload. Any matched
source not uploadable to Photos stays local. The matcher also checks the
audio of visually matched source videos; a separate soundtrack may still
need to be added by hand. Uploads make additional copies in Google Photos;
they are stored at original quality and may use account
storage. Photos API uploads can also be subject to Google's app/testing
limits. Google Photos accepts image and video formats; matched audio files
and file types it cannot store remain local paths in the YAML.

Google's API does not let the CLI turn on album sharing. After upload, the
CLI guides you to open that album in Google Photos, turn on link sharing
once, and paste the resulting link. It validates the link, then writes
`source:` references in the YAML using the album's remote item IDs for
uploaded photos and videos. No manual photo-by-photo album curation is
needed. The Takeout files can be
kept or deleted after matching; the YAML fetches remote album copies.

If the upload is interrupted, the album may contain some copies already;
inspect it in Google Photos before rerunning to avoid creating another
album or uploading duplicates.

You can use a shared Google Photos album as the candidate bank instead of a
local folder:

```bash
video-as-code reconstruct rendered.mp4 --album 'https://photos.app.goo.gl/…' -o output.yaml
```

The command downloads the album items to a temporary folder for matching, then
writes the matched originals as `source:` entries tied to that album. Nothing
from the temporary folder is needed afterward. Fetch the originals into the
project's `assets/` folder before building or rendering:

```bash
video-as-code fetch output.yaml
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
