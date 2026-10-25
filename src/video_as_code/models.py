"""Data structures: what's read from the YAML (Spec) and what the XML
generator consumes once validated and resolved to frames (Resolved)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .timecode import FrameRate

VALID_TRACK_TYPES = ("video", "audio")
VALID_CLIP_TYPES = ("video", "audio", "image", "animation", "title")


# ---------------------------------------------------------------------------
# As-is from the YAML (units: seconds, paths already resolved to Path)
# ---------------------------------------------------------------------------

@dataclass
class ProjectSpec:
    id: str
    title: str
    fps: float
    width: int
    height: int
    colorspace: int = 709
    default_image_duration: float = 4.0
    notes: Optional[str] = None
    # `project.sources`: where bin clips with `source:` come from (see
    # BinClipSpec.source). `album_url` is a Google Photos shared-album link
    # (gphotos.py); `album_name` the album's name, for a tool that creates
    # it and writes its link back as `url:` (albums.py) - until then
    # `fetch` can't get anything from it. `sources_dir` is where `fetch`
    # puts what it downloads, relative to the YAML.
    album_url: Optional[str] = None
    album_name: Optional[str] = None
    # `project.sources.reference`: links to the video this project remakes
    # (the old edit it's compared with and cuts shots out of), tried in
    # order like a bin clip's `url:`, and where `fetch` puts it, relative to
    # the YAML. build/render never read it.
    reference_urls: Optional[tuple[str, ...]] = None
    reference_path: str = "reference/reference.mp4"
    sources_dir: str = "assets"
    # `project.sources.prune_after_days`: before each render, delete files
    # the tool can bring back by itself (album items only derive: needs,
    # stale generated files) that no render has used for this long - see
    # autoprune.py. 0 turns it off.
    prune_after_days: float = 7.0
    # `project.loudness`: the loudness (LUFS) every clip with sound on an
    # audio track is brought to by a static gain - see loudness.py. None:
    # left as they are.
    loudness: Optional[float] = None


@dataclass(frozen=True)
class DeriveSpec:
    """A bin clip's `derive:`: its file is GENERATED with ffmpeg from
    another file (trimmed, reversed, re-timed, cropped, scaled, made
    louder) instead of being stored - so a hand-made cut of an album
    video can be regenerated after `fetch` rather than kept around. See
    derive.py for how and when it's generated and where it's cached.

    Exactly one input: `source` (an album item's original file name, like
    BinClipSpec.source - `fetch` downloads it even if no other bin clip
    uses it), `path` (a local file, relative to the YAML) or `clip`
    (another bin clip's id, which may itself be derived). `motion: true`
    takes, instead of the input itself, the short video a phone's motion
    photo (Google's `.MP.jpg`) carries inside it. Then, applied in
    this order: `rotate` (90, 180 or 270 degrees clockwise, of the picture as
    it shows - EXIF orientation applied; then every later operation, `crop`
    included, works on the turned picture), `trim` (source seconds [start, end)) and `cut` (source
    seconds [start, end) ranges REMOVED from the middle, picture and sound
    alike; both in the input's own seconds), `crop` (x, y, w, h,
    source pixels), `scale` (w, h; -1 keeps the aspect ratio),
    `devignette` (undoes a vignette already baked into the input - say a
    shot taken from an old edit that darkened its edges: how bright its
    corners are relative to its centre, 0 < x < 1, see derive.py), `fit`
    ((w, h, background): the whole picture made to fit inside w x h, the
    rest filled with `black` or with a blurred, enlarged copy of itself -
    `blur` - so a portrait photo fills a landscape frame without being
    cropped), `feather` (soft edges: the picture fades out over its last
    that many pixels on every side - to black, and with `fit: blur` also
    into the blurred copy around it), `vignette` (the reverse of
    `devignette`: darkens the edges,
    corners at that fraction of the centre's brightness), `fps`,
    `reverse` (picture and sound), `volume` (audio gain factor) and, last,
    `upscale` (4: enlarge a still or gif x4 with Real-ESRGAN, see
    upscale.py - slow, so stills and gifs only). `intra: true` encodes a
    video output with every frame a keyframe (all-intra): MLT's timewarp
    (`speed:` != 1) can hold one frame for a moment, or fail outright, near
    the end of a file with a long GOP / B-frames - all-intra avoids it at
    the cost of a bigger file."""

    source: Optional[str] = None
    path: Optional[Path] = None
    clip: Optional[str] = None
    trim: Optional[tuple[float, float]] = None
    cut: Optional[tuple[tuple[float, float], ...]] = None
    crop: Optional[tuple[int, int, int, int]] = None
    scale: Optional[tuple[int, int]] = None
    devignette: Optional[float] = None
    # `rotate`: 90, 180 or 270 degrees clockwise, first of all - for a photo whose
    # EXIF orientation is wrong (the camera's pixels were right; the tag turns them)
    rotate: Optional[int] = None
    fit: Optional[tuple[int, int, str]] = None  # (w, h, "blur" | "black")
    # `fit: {lighten}`: how much to brighten the blurred copy around the
    # picture (0-1: every channel times 1 + 5 x lighten, so its colours
    # stay) - for a dark picture, whose surround would otherwise look black
    fit_lighten: Optional[float] = None
    # `fit: {margin, radius, edge, shadow, dim}` (blur only): the picture as a card on
    # its blurred copy - at least `margin` px of it all round, corners rounded to
    # `radius` px, its edge softened over `edge` px, a soft `shadow` (0-1) under it,
    # and the blurred copy darkened by `dim` (0-1) so the card stands out
    fit_margin: Optional[int] = None
    fit_radius: Optional[int] = None
    fit_edge: Optional[int] = None
    fit_shadow: Optional[float] = None
    fit_dim: Optional[float] = None
    feather: Optional[int] = None  # px
    vignette: Optional[float] = None
    fps: Optional[float] = None
    reverse: bool = False
    volume: Optional[float] = None
    upscale: Optional[int] = None
    motion: bool = False
    intra: bool = False
    # `openshot`: libopenshot's effects, last of all (openshot_effects.py) -
    # each effect as JSON, as checked
    openshot: tuple[str, ...] = ()


@dataclass
class BinClipSpec:
    id: str
    path: Optional[Path]  # None for type: title - schema.py generates and fills it in
    type: Optional[str] = None  # overrides the extension-based autodetection ("sequence": a part's, parts.py)
    folder: Optional[str] = None  # bin folder name (root-level only), None = no folder
    sequence: Optional["ResolvedProject"] = None  # with type "sequence": the part's resolved timeline (parts.py)
    # Instead of `path:`: the item's ORIGINAL file name in the project's
    # shared album (`project.sources.album`), e.g. "PXL_20250830_085416309.mp4".
    # `video-as-code fetch` downloads it to <sources_dir>/<local name> -
    # `<capture date>_<id><ext>` by default, see sources_lock.local_name -
    # which is what `path` then points at.
    source: Optional[str] = None
    # Instead of `path:`/`source:`: links to download the file from, tried
    # in order until one works (see links.py: YouTube gives its audio,
    # Google Drive and plain http(s) the file as is) - for what isn't in
    # the album, like the music. `fetch` stores it as `<id><ext>` (the
    # downloaded file's extension, recorded in the lock).
    url: Optional[list[str]] = None
    # Only with `source:`/`url:`: the local file name to store it under,
    # instead of the default (see sources_lock.local_name/link_local_name).
    local_name: Optional[str] = None
    # Instead of `path:`/`source:`: generate the file with ffmpeg from
    # another one (see DeriveSpec). `path` then points at the generated
    # file, cached under <sources_dir>/.derived/ (see derive.py).
    derive: Optional[DeriveSpec] = None
    # title-only fields (type: title), ignored otherwise:
    text: Optional[str] = None
    font: str = "Sans"
    font_size: int = 48
    font_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    background: tuple[int, int, int, int] = (0, 0, 0, 0)
    # (Qt 6's weights: 400 normal, 700 bold - see titles.py)
    font_weight: int = 400
    italic: bool = False
    outline: float = 0.0  # pixels around each letter, in outline_color
    outline_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    shadow: Optional[tuple[tuple[int, int, int, int], int, int, int]] = None  # (color, blur, x, y)


@dataclass(frozen=True)
class MeltService:
    """An MLT filter or transition by its service name, with its
    properties as written to the XML (see meltpass.py)."""
    service: str
    properties: tuple[tuple[str, str], ...] = ()


@dataclass
class ClipPlacementSpec:
    clip: str
    start: float
    in_: float = 0.0
    out: Optional[float] = None
    duration: Optional[float] = None
    # Trim by *source* seconds regardless of `speed:` - unlike `in:`/
    # `out:` (relative to the already-warped timeline when `speed:` is
    # set, matching kdenlive itself), these always mean "seconds into
    # the real source file," dividing by `speed` internally so the
    # resulting warped duration comes out right without the caller
    # doing that math by hand. Both or neither; mutually exclusive with
    # `in:`/`out:`/`duration:`. Exception: with `speed_ramp:`,
    # `source_in:` ALONE (plus `duration:`/`out:`) is allowed and means
    # "start the ramp this many real seconds into the source" - see
    # `speed_ramp`.
    source_in: Optional[float] = None
    source_out: Optional[float] = None
    fade_in: Optional[float] = None
    fade_out: Optional[float] = None
    # Starting volume level (0..1) for an audio fade_in, instead of always
    # ramping up from silence - see xmlgen.py's _add_audio_fade_filters.
    # Ignored for video clips and meaningless without fade_in.
    fade_in_level: Optional[float] = None
    # Ending volume level (0..1) for an audio fade_out, instead of always
    # ramping down to silence - see xmlgen.py's _add_audio_fade_filters.
    # Ignored for video clips and meaningless without fade_out.
    fade_out_level: Optional[float] = None
    # Flat volume multiplier for the whole clip (1.0 = unchanged, 2.0 =
    # doubled, etc.) - a static gain, independent of fade_in/fade_out.
    # Ignored for video clips.
    gain: Optional[float] = None
    # This clip's own `loudness:` target (LUFS), instead of the project's;
    # False leaves it as it is. None: the project's (loudness.py).
    loudness: Optional[float | bool] = None
    mute: bool = False
    speed: Optional[float] = None
    preserve_pitch: bool = False
    # Shorthand for the "play forward, then immediately play the same
    # segment backward" repeat pattern often done by hand
    # (two placements per clip, written out manually): expanded
    # at parse time (schema.py's _parse_track) into two placements - this
    # one as written (boomerang stripped), plus an auto-generated second
    # one right after it, no fade_in (a hard cut into the repeat, matching
    # the hand-written convention), and mute forced true (reversed audio
    # is rarely wanted; set `mute: false` explicitly on THIS placement if
    # a reversed audio repeat is actually intended).
    #
    # The reverse half does NOT use negative `speed:` (MLT's own negative-
    # speed timewarp producer) - real render testing (frame-by-frame, on
    # real-world upscaled gifs) found it reliably corrupts a chunk of the
    # reverse playback (a solid-color/striped garbage frame at the seam,
    # then anywhere from a few frames to most of the reverse half showing
    # wrong/garbled content before it catches up) on large images. Root
    # cause confirmed by reading MLT's own producer_timewarp.c: negative
    # speed re-seeks the wrapped producer backward one frame at a time
    # every frame, and the qimage/gif producer's own decode path isn't
    # robust against a strictly-decreasing seek pattern at large
    # resolutions - not something fixable from this project's XML output
    # (see validate_and_resolve's `_render_boomerang_reverse_source` for
    # the actual workaround: pre-render an already-reversed derivative
    # file with ffmpeg once, cache it, and play *that* forward at the
    # same positive speed - sidesteps MLT's reverse-seek path entirely).
    # Deliberately narrow v1 scope (same call speed_ramp's docstring
    # made): requires an explicit `out:`/`duration:`/`source_in:`+
    # `source_out:` so the repeat's start can be computed without probing
    # the source file, and is mutually exclusive with `speed_ramp`
    # (reversing a multi-segment ramp isn't attempted this round).
    boomerang: bool = False
    # Only meaningful with `boomerang: true`. The forward half ends on the window's last picture and the
    # reverse half starts on that same picture, so if the source HOLDS its last frame (a gif's long final
    # delay, a video's frozen tail) the turnaround shows it twice as long as the source ever did - a
    # visible "stall" in the middle of the repeat. With this on (the default), validate_and_resolve
    # measures that hold (probe.detect_trailing_hold_seconds) and moves `source_out` of BOTH halves back
    # by half of it, so the turnaround shows the held picture for exactly its original length once.
    # Note this makes the placement shorter on the timeline than its own source_in/source_out/speed
    # say (by hold / 2 / speed per half); nothing after it moves (starts are explicit), so leave room
    # for it or re-fit the following clips. Holds under probe.HOLD_MIN_S (0.1s) are left alone.
    # Only applies to the `source_in:`+`source_out:` form; set `boomerang_trim_hold: false` to opt out.
    boomerang_trim_hold: bool = True
    # Internal only (never set from YAML - schema.py's _expand_boomerangs
    # sets this on the placement it synthesizes for `boomerang:`'s reverse
    # half): tells validate_and_resolve to substitute this placement's bin
    # clip for a pre-reversed derivative file instead of resolving
    # `self.clip` directly - see `boomerang`'s docstring above for why.
    _boomerang_reverse_of: Optional[str] = None
    # Speed ramp (mlt_service "timeremap", a <link> on a <chain> - see
    # xmlgen.py's _add_speed_ramp_link) - a real, non-flat speed curve,
    # distinct from `speed`'s single multiplier (mutually exclusive with
    # it). `[(at_seconds, speed_multiplier), ...]`, sorted, first at 0:
    # each entry marks where a new constant-speed segment starts: speed
    # holds flat from that keyframe to the next (or to the clip's own
    # end for the last one) - a ramp is several flat segments in
    # sequence, not a smoothly-eased curve. Deliberately narrow v0.7
    # scope: starts reading at the source's own beginning unless
    # `source_in:` is set ALONE (no `source_out:` - duration + the ramp
    # already decide where reading stops), which offsets where the ramp
    # starts reading in real source seconds; `in:` isn't supported
    # alongside it. Needs an explicit `out:`/`duration:`
    # (no auto-inferred "use all available source" default like flat
    # `speed:` gets, since that would need solving for where a
    # multi-segment ramp runs out of source material rather than a
    # single division).
    speed_ramp: Optional[list[tuple[float, float]]] = None
    speed_ramp_image_mode: str = "nearest"  # timeremap's own "nearest" | "blend"
    position: Optional[tuple[float, float]] = None
    size: Optional[tuple[float, float]] = None
    # Animated picture-in-picture (multi-keyframe qtblend `rect`, the
    # same per-track filter the static position:/size: pair already
    # drives - see xmlgen.py's _add_video_default_filter). Mutually
    # exclusive with the static position:/size: pair (a keyframe list
    # fully replaces them, no partial mixing). `[(at_seconds, x, y, w,
    # h), ...]`, pixels (same convention as static position:/size:, not
    # Ken Burns' percentages), sorted, first at 0.
    position_keyframes: Optional[list[tuple[float, float, float, float, float]]] = None
    # Edge crop (MLT's own `crop` filter, matching kdenlive's own "Edge
    # Crop" effect) - (top, right, bottom, left) pixel margins trimmed
    # from the *source*, then rescaled back to fill the whole canvas
    # (see xmlgen.py's _add_crop_filters for why the rescale step is
    # mandatory here, not optional/cosmetic). Mutually exclusive with
    # position:/size:/position_keyframes: (the rescale step *is* this
    # clip's own qtblend positioning - combining it with custom PiP
    # placement would need deciding which one "wins" the canvas, not
    # attempted this round).
    crop: Optional[tuple[float, float, float, float]] = None
    # Freeze-frame (mlt_service "freeze", kdenlive-native, matching
    # kdenlive's own "Freeze" effect exactly - verified by real render:
    # `melt clip.mp4 -filter freeze frame=N freeze_after=1` genuinely
    # holds frame N for the rest of playback instead of continuing, and
    # symmetrically for freeze_before/plain). `freeze_at` is seconds
    # within *this clip's own* post-trim timeline (0 = the clip's first
    # visible frame), matching how ken_burns_from/focus_rect are already
    # scoped to "this clip's own duration", not the source file's.
    # `freeze_before`/`freeze_after` false (the default) freezes the
    # *whole* clip on that one frame; setting one plays normally on the
    # other side of `freeze_at` and only freezes past it - see MLT's own
    # filter_freeze.c for the exact do_freeze condition this mirrors.
    freeze_at: Optional[float] = None
    freeze_before: bool = False
    freeze_after: bool = False
    reveal_style: Optional[str] = None  # None | "iris" | "flip" | "glitch" | "delaygrab" | "cairogradient" - see ResolvedPlacement
    chroma_key: Optional[str] = None  # color to key out (e.g. "#00ff00"), None = no chroma-key
    chroma_key_distance: float = 0.35  # frei0r.bluescreen0r "Distance" - only used when chroma_key is set
    # Burned-in text overlay (mlt_service "dynamictext") on an *existing*
    # clip - distinct from titles.py's standalone type: title bin clips.
    # `overlay_text` may include dynamictext's own keywords (#timecode#,
    # #frame#, #localtime#...), same as MLT itself.
    overlay_text: Optional[str] = None
    overlay_font: str = "Sans"
    overlay_font_size: int = 48
    overlay_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    overlay_background: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Ken Burns pan+zoom (mlt_service "affine" as a single-clip filter,
    # same mechanism kdenlive's own "Position and Zoom" effect uses -
    # see xmlgen.py's _add_ken_burns_filter). Each is (x%, y%, w%, h%) of
    # the frame - the filter animates from `ken_burns_from` at the clip's
    # start to `ken_burns_to` at its end. Both or neither, never just one.
    ken_burns_from: Optional[tuple[float, float, float, float]] = None
    ken_burns_to: Optional[tuple[float, float, float, float]] = None
    # Focus-pull (mlt_service "pillar_echo", kdenlive-native, Meltytech):
    # keeps focus_rect sharp, blurs everything outside it. (x%, y%, w%, h%);
    # None = no filter. Static for the clip's whole duration - kdenlive's
    # own `rect` param supports keyframing but that's not exposed here
    # (same narrower-scope call overlay_text's fixed layout made).
    focus_rect: Optional[tuple[float, float, float, float]] = None
    focus_blur: float = 4.0  # blur radius, percent of image width (kdenlive default)
    # `dance` (audio-reactive auto-zoom) was investigated and dropped -
    # see ROADMAP.md: real kdenlive's own per-track dual-playlist
    # architecture sets hide="audio" on every video track's inner
    # sub-tracks (confirmed both in this generator's output and in real
    # user .kdenlive projects), which structurally cuts off audio from
    # that track's own clip-level filters - dance needs audio+video on
    # the same frame to react to, so it can never work on a video-track
    # clip here, not a "no visible effect" case like broken-tv.
    # "Old film" look family (v0.8) - four independent, freely
    # combinable kdenlive-native filters (Marco Gittler, GPL), each a
    # plain on/off toggle using kdenlive's own filter defaults (no
    # per-parameter overrides in this first cut, same narrow-scope call
    # overlay_text's fixed layout made). `old_film` is `oldfilm`
    # (Y-jitter + brightness flicker + patchy "uneven develop" - the
    # strongest match for a classic "random black burned patches"
    # look); `film_dust`/`film_scratches`/`film_grain` are the `dust`/
    # `lines`/`grain` filters respectively (named "scratches" not
    # "lines" here since the latter is a confusing, over-generic word
    # for a user-facing YAML field).
    old_film: Optional[bool] = None
    film_dust: Optional[bool] = None
    # `film_dust: {style, size, count}` instead of `true`: `svg` (the
    # default) draws MLT's dust pictures, `size` % of the clip's width
    # across; `dots` draws small round specks instead, `size` px at most in
    # radius (see mltdata.py for how). `count`: at most that many per frame.
    film_dust_style: str = "svg"
    film_dust_size: int = 2
    film_dust_count: int = 10
    # `pictures:` (svg only): which pictures to draw from instead of MLT's
    # five - each MLT's own by name (`dust1`..`dust5`) or an .svg path, one
    # picked at random per speck, so a repeated entry comes up more often
    film_dust_pictures: Optional[tuple[str, ...]] = None
    # `hold:` the same specks for that many frames running (default 1: new
    # ones every frame) - see xmlgen._old_film_span
    film_dust_hold: int = 1
    film_scratches: Optional[bool] = None
    film_grain: Optional[bool] = None
    # `film_grain: {noise, contrast, brightness}` instead of `true`: `noise`
    # how strong, `contrast`/`brightness` in % with 100 meaning unchanged -
    # what a map defaults to; `true` keeps kdenlive's 40/160/70, which also
    # darkens and hardens the whole picture.
    film_grain_noise: int = 40
    film_grain_contrast: int = 160
    film_grain_brightness: int = 70
    # Professional color grading (mlt_service "lift_gamma_gain",
    # Meltytech, LGPLv2.1) - "lift=shadows, gamma=midtones,
    # gain=highlights", the classic three-way grading formula. Any
    # subset of the three groups may be present; an omitted group keeps
    # MLT's own neutral default for it (lift 0,0,0; gamma 1,1,1; gain
    # 1,1,1 - together a no-op), so at least one group is required (no
    # dead no-op filter emitted, same discipline `old_film`'s
    # false-means-no-filter toggles hold to).
    color_grade: Optional[dict[str, tuple[float, float, float]]] = None
    # `color_grade_keyframes: [{at: 0, lift: [..]}, {at: 1.5, lift: [..]}]`:
    # a second grade on top of `color_grade`, animated - each group eased
    # linearly from one keyframe to the next (`at`: seconds into the clip,
    # the first at 0; every keyframe with the same groups), held after the
    # last. E.g. a brightness swell that rises and falls over several clips.
    color_grade_keyframes: Optional[list[tuple[float, dict[str, tuple[float, float, float]]]]] = None
    # (LUT-based grading: `lut:`, below.)
    # Video stabilization (mlt_service "deshake", Vid.Stab/GPLv2) - a
    # plain on/off toggle at MLT's own defaults for all eleven
    # parameters, no per-parameter overrides in this first cut (same
    # narrow-scope call `old_film`'s toggles made). Unlike `vidstab`
    # (the two-pass version, needs an intermediate `.trf` results file
    # this project's single-pass-static-XML architecture has no
    # mechanism for), `deshake` is single-pass. Verified by real render
    # (a synthetic shaky source, frame-to-frame feature movement
    # measured before/after): identical, clean result on both this
    # project's preferred from-source dev melt build and the system's
    # stable one - no `crop`/`lut3d`-style build-specific issue.
    stabilize: Optional[bool] = None
    # `lut:` - a 3D LUT file (.cube/.3dl/.dat/.m3d) applied to the
    # picture, after color_grade: `avfilter.lut3d`, kdenlive's own "Apply
    # LUT". Dropped once (v0.9: a from-source melt's avfilter bridge left
    # it doing nothing), it works on both melts since (7.36 and 7.41,
    # checked by an inverting LUT). Relative to the project file.
    lut: Optional[str] = None
    lut_interp: str = "tetrahedral"  # one of LUT_INTERPS
    # `glsl:` - a GLSL effect on the picture (glfilters.py), as written:
    # resolved (a .glsl file against the project's folder) by schema.py
    glsl: Any = None
    # `melt:` - any MLT filter by service name, after the clip's own
    # (see meltpass.py)
    melt_filters: tuple["MeltService", ...] = ()


@dataclass
class TrackSpec:
    type: str
    name: Optional[str]
    clips: list[ClipPlacementSpec] = field(default_factory=list)
    pan: Optional[float] = None  # audio only: -1.0 (full left) .. 1.0 (full right)
    hidden: bool = False
    locked: bool = False
    audio_muted: bool = False  # whole-track audio mute (hide's bit 2) - distinct from per-clip mute:


@dataclass
class WipeTransitionSpec:
    """One overlap window between two adjacent clips on alternating video
    tracks, produced by scenes.py's `transition: wipe`. Unlike `dissolve`
    (a per-clip alpha filter fakes the cross-fade), a wipe needs a real
    two-source MLT transition (`mlt_service: luma`) so the luma map can
    decide, per pixel, which of the two overlapping clips shows - so this
    carries enough to let xmlgen.py emit that transition directly, instead
    of being folded into `ClipPlacementSpec` like a fade is."""
    track_a: str
    track_b: str
    start: float
    end: float
    luma: str  # one of lumas.AVAILABLE_LUMAS, or "openshot/<name>" (see lumas.py)
    softness: float = 0.0  # MLT luma's: 0 a hard edge, 1 the softest
    reverse: bool = False  # the map's light parts first instead of its dark ones


@dataclass
class MeltTransitionSpec:
    """`transition: melt/<service>` on a scene, `type: melt/<service>` on
    `transitions:` - any MLT transition between two tracks over a window,
    emitted like a wipe (see meltpass.py)."""
    track_a: str
    track_b: str
    start: float
    end: float
    melt: MeltService


AFFINE_STYLES = ("zoom", "slide")
LUT_INTERPS = ("nearest", "trilinear", "tetrahedral")  # avfilter.lut3d's
LUT_SUFFIXES = (".cube", ".3dl", ".dat", ".m3d")  # shared with scenes.py and schema.py's `transitions:` parsing


@dataclass
class AffineTransitionSpec:
    """One overlap window between two adjacent clips on alternating video
    tracks, produced by scenes.py's `transition: affine` (`style: zoom` or
    `style: slide`). Like `wipe`, this needs a real two-source MLT
    transition (`mlt_service: affine`) rather than a per-clip alpha
    filter, since it geometrically transforms the incoming clip (scaled
    rect for zoom, offset rect for slide) as it's composited over the
    outgoing one - a per-clip filter can't do a two-source geometric
    reveal any more than it could a two-source luma wipe."""
    track_a: str
    track_b: str
    start: float
    end: float
    style: str  # "zoom" | "slide"


@dataclass
class GuideSpec:
    """A timeline marker/guide - `kdenlive:sequenceproperties.guides` on the
    sequence tractor, a JSON array of `{"pos": <frame>, "comment": <str>,
    "type": <int>}` (confirmed against kdenlive's own source,
    `MarkerListModel::toJson`/`importFromJson` in
    src/bin/model/markerlistmodel.cpp - not modeled by analogy). `category`
    is kdenlive's marker-category index (color-coded in the UI); 0 is
    always a valid fallback category."""
    at: float
    text: str
    category: int = 0


@dataclass
class ProjectDoc:
    project: ProjectSpec
    bin: list[BinClipSpec]
    tracks: list[TrackSpec]
    source_path: Path
    raw_extras: Optional[dict] = None  # see reverse.py: `x_kdenlive_raw` block
    scenes_raw: list = field(default_factory=list)  # uncompiled; see scenes.py
    wipe_transitions: list[WipeTransitionSpec] = field(default_factory=list)
    affine_transitions: list[AffineTransitionSpec] = field(default_factory=list)
    melt_transitions: list[MeltTransitionSpec] = field(default_factory=list)
    guides: list[GuideSpec] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validated and resolved to frames: what xmlgen.py consumes
# ---------------------------------------------------------------------------

@dataclass
class ResolvedBinClip:
    id: str
    numeric_id: int
    path: Path
    kind: str  # video | audio | image
    duration_frames: int
    folder: Optional[str] = None
    # a part's own timeline, placed as a clip (parts.py): kdenlive's nested
    # sequence, by its uuid
    sequence: Optional["ResolvedProject"] = None
    sequence_uuid: Optional[str] = None
    # a derive: of libopenshot's effects alone (openshot_effects.py): its
    # input and the effects, so an OpenShot project (osp.py) can apply
    # them itself rather than use the generated file
    openshot_input: Optional[Path] = None
    openshot_effects: tuple[str, ...] = ()


@dataclass
class ResolvedPlacement:
    bin_clip: ResolvedBinClip
    start_frame: int
    in_frame: int
    out_frame: int  # last included frame (MLT convention)
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    fade_in_level: float = 0.0
    fade_out_level: float = 0.0
    gain: Optional[float] = None
    mute: bool = False
    speed: Optional[float] = None
    preserve_pitch: bool = False
    # Speed ramp - see ClipPlacementSpec. `[(at_frame, speed), ...]`,
    # frame-based (seconds already resolved to frames), first always 0.
    speed_ramp: Optional[list[tuple[int, float]]] = None
    speed_ramp_image_mode: str = "nearest"
    # Real source seconds the ramp starts reading from (the placement's
    # `source_in:`, 0 when unset) - see ClipPlacementSpec.speed_ramp.
    speed_ramp_source_offset_s: float = 0.0
    rect: Optional[tuple[float, float, float, float]] = None  # x y w h in pixels
    # Animated PiP - see ClipPlacementSpec. `[(at_frame, x, y, w, h), ...]`.
    position_keyframes: Optional[list[tuple[int, float, float, float, float]]] = None
    # Edge crop - see ClipPlacementSpec. (top, right, bottom, left) pixels.
    crop: Optional[tuple[float, float, float, float]] = None
    # Freeze-frame - see ClipPlacementSpec. `freeze_at_frame` already
    # resolved to a frame offset within this clip's own post-trim
    # timeline (0 = clip's first frame).
    freeze_at_frame: Optional[int] = None
    freeze_before: bool = False
    freeze_after: bool = False
    # which filter `fade_in_frames`/`fade_out_frames` should drive instead
    # of the plain brightness alpha fade. "iris" (frei0r.alphaspot) and
    # "flip" (frei0r.3dflippo) are real two-source-*looking* reveals built
    # from a single-clip filter composited via the qtblend this project
    # already emits per video track, same mechanism `dissolve` itself
    # uses (only the "top" clip needs the filter). "glitch"
    # (frei0r.glitch0r), "delaygrab" (frei0r.delaygrab), and
    # "cairogradient" (frei0r.cairogradient) are different again - none
    # writes any alpha at all (confirmed by render for all three: a
    # colored track below never shows through), so none is a reveal/
    # composite in any sense, just a brief filter burst dressing a plain
    # cut (see scenes.py's `BURST_TRANSITIONS`/`BURST_TRACK_NAME`).
    reveal_style: Optional[str] = None  # None | "iris" | "flip" | "glitch" | "delaygrab" | "cairogradient"
    # Green/blue-screen compositing (frei0r.bluescreen0r) - unlike
    # reveal_style, this spans the clip's *whole* duration, not a brief
    # transition window, and composites through the same per-track
    # qtblend chain every video track already gets against the ones
    # below it (no new mechanism needed - verified by real render).
    chroma_key: Optional[str] = None
    chroma_key_distance: float = 0.35
    # Burned-in text overlay (mlt_service "dynamictext") - see
    # ClipPlacementSpec.
    overlay_text: Optional[str] = None
    overlay_font: str = "Sans"
    overlay_font_size: int = 48
    overlay_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    overlay_background: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Ken Burns pan+zoom - see ClipPlacementSpec.
    ken_burns_from: Optional[tuple[float, float, float, float]] = None
    ken_burns_to: Optional[tuple[float, float, float, float]] = None
    # Focus-pull - see ClipPlacementSpec.
    focus_rect: Optional[tuple[float, float, float, float]] = None
    focus_blur: float = 4.0
    # "Old film" look family - see ClipPlacementSpec.
    old_film: Optional[bool] = None
    film_dust: Optional[bool] = None
    film_dust_style: str = "svg"
    film_dust_size: int = 2
    film_dust_count: int = 10
    # MLT's own pictures by name, the others as absolute paths
    film_dust_pictures: Optional[tuple[str, ...]] = None
    film_dust_hold: int = 1
    film_scratches: Optional[bool] = None
    film_grain: Optional[bool] = None
    film_grain_noise: int = 40
    film_grain_contrast: int = 160
    film_grain_brightness: int = 70
    # Color grading - see ClipPlacementSpec.
    color_grade: Optional[dict[str, tuple[float, float, float]]] = None
    # (clip-relative frame, groups) - see ClipPlacementSpec.color_grade_keyframes
    color_grade_keyframes: Optional[list[tuple[int, dict[str, tuple[float, float, float]]]]] = None
    # Video stabilization - see ClipPlacementSpec.
    stabilize: Optional[bool] = None
    lut: Optional[str] = None  # absolute - see ClipPlacementSpec
    lut_interp: str = "tetrahedral"
    glsl_filter: Optional["MeltService"] = None  # see ClipPlacementSpec.glsl
    melt_filters: tuple["MeltService", ...] = ()  # see ClipPlacementSpec

    @property
    def length_frames(self) -> int:
        return self.out_frame - self.in_frame + 1


@dataclass
class ResolvedTrack:
    kind: str
    name: str
    placements: list[ResolvedPlacement]
    pan: Optional[float] = None
    hidden: bool = False
    locked: bool = False
    audio_muted: bool = False

    @property
    def end_frame(self) -> int:
        if not self.placements:
            return 0
        last = self.placements[-1]
        return last.start_frame + last.length_frames


@dataclass
class ResolvedWipeTransition:
    track_a: str
    track_b: str
    start_frame: int
    end_frame: int  # last included frame (MLT convention, like ResolvedPlacement.out_frame)
    luma: str
    softness: float = 0.0
    reverse: bool = False


@dataclass
class ResolvedAffineTransition:
    track_a: str
    track_b: str
    start_frame: int
    end_frame: int  # last included frame (MLT convention, like ResolvedPlacement.out_frame)
    style: str  # "zoom" | "slide"


@dataclass
class ResolvedMeltTransition:
    track_a: str
    track_b: str
    start_frame: int
    end_frame: int  # last included frame
    melt: MeltService


@dataclass
class ResolvedGuide:
    frame: int
    text: str
    category: int


@dataclass
class ResolvedProject:
    project_id: str
    title: str
    fps: FrameRate
    width: int
    height: int
    colorspace: int
    bin_clips: list[ResolvedBinClip]
    tracks: list[ResolvedTrack]
    source_dir: Path
    wipe_transitions: list[ResolvedWipeTransition] = field(default_factory=list)
    affine_transitions: list[ResolvedAffineTransition] = field(default_factory=list)
    melt_transitions: list[ResolvedMeltTransition] = field(default_factory=list)
    guides: list[ResolvedGuide] = field(default_factory=list)
    notes: Optional[str] = None
    part_path: str = ""  # a part's, in a project of several files (parts.py); "" for the main one

    @property
    def total_frames(self) -> int:
        return max((t.end_frame for t in self.tracks), default=0)
