"""`film_dust: {style: dots}`: MLT's `dust` filter draws small round
specks - darker and lighter ones, up to `maxdiameter` PIXELS in radius,
like the dust on an old print - but only when it finds none of its own
dust pictures (`<MLT_DATA>/oldfilm/dust*.svg`); with them, which is
always on a normal install, it draws those, `maxdiameter` PERCENT of the
clip's width across (at least 1%, ~18 px on a 1080p-wide clip - and
shaped like blots, fluff and hairs). There's no property to choose.

So for a render that wants dots, melt runs with MLT_DATA pointing at a
copy of its data folder made of symlinks, minus those pictures
(dots_data_dir). It's a per-run setting, hence one dust style per
project (schema.py rejects a mix). The .kdenlive itself doesn't change:
opened in kdenlive, it shows the pictures.

`film_dust: {pictures: [...]}` uses the same swap the other way: a copy
whose dust pictures are the chosen ones (MLT's own by name, or the
project's .svg files) - e.g. without dust2, a wide blot that comes out
white, or with a small round speck, padded so that `size` % of the width
across makes it a few pixels.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from .models import ResolvedProject

_DUST_PICTURES = "dust*.svg"


def wants_dots(project: ResolvedProject) -> bool:
    return any(p.film_dust and p.film_dust_style == "dots" for t in project.tracks for p in t.placements)


def wanted_pictures(project: ResolvedProject) -> Optional[tuple[str, ...]]:
    """The dust pictures the project chose (schema.py allows one set)."""
    for t in project.tracks:
        for p in t.placements:
            if p.film_dust and p.film_dust_pictures:
                return p.film_dust_pictures
    return None


def find_data_dir(melt: str) -> Optional[Path]:
    """MLT's data folder for this melt: MLT_DATA if set, else
    <prefix>/share/mlt-7 (or /mlt) next to its bin/ - where an MLT built
    from source and the distribution's package both keep it."""
    candidates = []
    if os.environ.get("MLT_DATA"):
        candidates.append(Path(os.environ["MLT_DATA"]))
    prefix = Path(os.path.realpath(melt)).parent.parent
    candidates += [prefix / "share" / "mlt-7", prefix / "share" / "mlt"]
    for c in candidates:
        if (c / "oldfilm").is_dir():
            return c
    return None


def dots_data_dir(data_dir: Path, cache_root: Path) -> Path:
    """A copy of `data_dir` without the dust pictures, as symlinks (so
    nothing is duplicated), under `cache_root`; rebuilt if missing."""
    return pictures_data_dir(data_dir, cache_root, ())


def pictures_data_dir(data_dir: Path, cache_root: Path, pictures: tuple[str, ...]) -> Path:
    """A copy of `data_dir` (symlinks) whose dust pictures are `pictures`:
    MLT's own by name (`dust1`), the others paths to .svg files; none at
    all draws dots. Keyed by the folder and the pictures' contents."""
    h = hashlib.sha1(str(data_dir.resolve()).encode())
    sources = []
    for p in pictures:
        src = data_dir / "oldfilm" / f"{p}.svg" if "/" not in p else Path(p)
        sources.append(src.resolve())
        h.update(b"\0" + src.read_bytes())
    key = h.hexdigest()[:10]
    target = cache_root / (f"mlt-data-dust-{key}" if pictures else f"mlt-data-dots-{key}")
    if (target / "oldfilm").is_dir():
        return target
    tmp = target.with_name(target.name + ".tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    for entry in data_dir.iterdir():
        if entry.name != "oldfilm" and not (tmp / entry.name).exists():
            (tmp / entry.name).symlink_to(entry.resolve())
    (tmp / "oldfilm").mkdir(exist_ok=True)
    pictures = set((data_dir / "oldfilm").glob(_DUST_PICTURES))
    for entry in (data_dir / "oldfilm").iterdir():
        if entry not in pictures and not (tmp / "oldfilm" / entry.name).exists():
            (tmp / "oldfilm" / entry.name).symlink_to(entry.resolve())
    # the filter lists dust*.svg: a repeated picture is a second name for it
    for i, src in enumerate(sources, 1):
        link = tmp / "oldfilm" / f"dust{i:02d}.svg"
        if not link.exists():
            link.symlink_to(src)
    tmp.rename(target)
    return target


def default_cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "video-as-code"
