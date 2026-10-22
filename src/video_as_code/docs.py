"""`video-as-code docs`: the documentation as a local web - the README and
docs/*.md as HTML pages, with the gallery (gallery.py) as one more of
them - opened in the browser.

Built on the user's machine, from this checkout's Markdown and with the
gallery's real renders (their melt, their plugins, their wallpapers),
under the user cache next to the gallery:

    ~/.cache/video-as-code/docs/index.html            the README
    ~/.cache/video-as-code/docs/project-yaml.html     the reference...
    ~/.cache/video-as-code/gallery/index.html         the gallery

The two link to each other: each key of the reference that the gallery
has a card for (`transition`, `luma`, `lut`, `glsl`, `melt`, derive:'s
`openshot`...) links to its first card, and each card to its key. The
README stays as it is: GitHub's front page, rendered here too.

`video-as-code docs --public <folder>` makes a copy to publish (GitHub
Pages, say): built apart (~/.cache/video-as-code/public/), its gallery
over generated pictures rather than the wallpapers' photos, without
OpenShot's wipe shapes (GPL) or the user's own LUTs, and only the pages
and the previews they show copied to the folder - with an index.html
that goes to the docs and a .nojekyll, for Pages to serve it as it is.

Markdown to HTML with Python-Markdown (the optional `docs` extra:
`pip install 'video-as-code[docs]'`).
"""
from __future__ import annotations

import datetime
import html
import json
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from . import gallery
from .errors import KdenliveAsCodeError
from .i18n import msg
from .mltdata import default_cache_root

DOCS_DIR = "docs"
SOURCE = Path(__file__).resolve().parents[2]  # (the checkout: README.md, docs/)

# page -> its Markdown, in the order of the bar at the top
PAGES = {
    "index.html": "README.md",
    "making-a-video.html": "docs/making-a-video.md",
    "project-yaml.html": "docs/project-yaml.md",
    "reconstructing.html": "docs/reconstructing.md",
}
REFERENCE = "project-yaml.html"
PUBLIC_DIR = "public"  # under the cache root: the published copy's own cache
SITE_MARK = ".video-as-code-site"  # in a folder --public wrote, so it may write it again  # (the page with the keys the gallery's cards link to)


def _markdown(text: str) -> str:
    try:
        import markdown
    except ImportError as exc:
        raise KdenliveAsCodeError(msg("docs.markdown_missing")) from exc
    return markdown.markdown(text, extensions=["tables", "fenced_code", "toc"], output_format="html")


def _title(text: str, fallback: str) -> str:
    found = re.search(r"(?m)^# (.+)$", text)
    return re.sub(r"[`*]", "", found.group(1)).strip() if found else fallback


def _relink(body: str) -> str:
    """Links (and code spans) to README.md / docs/*.md to their pages."""
    pages = {md: page for page, md in PAGES.items()}
    pages.update({Path(md).name: page for page, md in PAGES.items()})

    def link(m: re.Match) -> str:
        target, anchor = m.group(2), m.group(3) or ""
        page = pages.get(target) or pages.get(target.removeprefix("../"))
        return f'{m.group(1)}"{page}{anchor}"' if page else m.group(0)

    body = re.sub(r'(href=)"((?:\.\./)?(?:docs/)?[\w.-]+\.md)(#[\w-]*)?"', link, body)
    return re.sub(r"<code>((?:docs/)?[\w.-]+\.md)</code>",
                  lambda m: f'<a href="{pages[m.group(1)]}"><code>{m.group(1)}</code></a>' if m.group(1) in pages else m.group(0),
                  body)


_KEY_ROW = re.compile(r"<tr>\s*<td><code>([a-z_]+)</code>")


def key_anchors(body: str) -> tuple[str, set[str]]:
    """Each reference table row whose first cell is a key gets an id,
    `key-<name>` (its first row, if several tables have it); the body
    with them, and the keys."""
    seen: set[str] = set()

    def anchor(m: re.Match) -> str:
        key = m.group(1)
        if key in seen:
            return m.group(0)
        seen.add(key)
        return m.group(0).replace("<tr>", f'<tr id="key-{key}">', 1)

    return _KEY_ROW.sub(anchor, body), seen


def _to_gallery(body: str, cards: dict[str, str]) -> str:
    """A link to the gallery after each key that has a card."""
    label = html.escape(msg("docs.to_gallery"))

    def add(m: re.Match) -> str:
        key = m.group(1)
        if key not in cards:
            return m.group(0)
        return f'{m.group(0)} <a class="to-gallery" href="../{gallery.GALLERY_DIR}/index.html#{cards[key]}" title="{label}">▶</a>'

    return re.sub(r'<tr id="key-([a-z_]+)">\s*<td><code>[a-z_]+</code>', add, body)


def build(
    own: Optional[Path] = None,
    cache_root: Optional[Path] = None,
    on_progress: Callable[[str], None] = lambda text: None,
    source: Path = SOURCE,
    public: bool = False,
) -> Path:
    """Writes the pages and the gallery (rendering what isn't cached yet);
    returns the first page's path. `public`: to publish (see publish)."""
    if not (source / "README.md").is_file():
        raise KdenliveAsCodeError(msg("docs.source_missing", path=source))
    root = (cache_root or default_cache_root()) / DOCS_DIR
    root.mkdir(parents=True, exist_ok=True)
    bodies: dict[str, tuple[str, str]] = {}
    keys: set[str] = set()
    for page, md in PAGES.items():
        path = source / md
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        body = _relink(_markdown(text))
        if page == REFERENCE:
            body, keys = key_anchors(body)
        bodies[page] = (_title(text, page), body)
    doc_links = {key: f"../{DOCS_DIR}/{REFERENCE}#key-{key}" for key in keys}
    gallery_page = gallery.build(own=own, cache_root=cache_root, on_progress=on_progress,
                                 doc_links=doc_links, docs_home=f"../{DOCS_DIR}/index.html", public=public)
    cards = json.loads((gallery_page.parent / gallery.DOC_CARDS).read_text(encoding="utf-8"))
    nav = [(page, title) for page, (title, _) in bodies.items()]
    note = msg("docs.public_note", date=datetime.date.today().isoformat(), melt=gallery._melt_version()) if public else ""
    for page, (title, body) in bodies.items():
        if page == REFERENCE:
            body = _to_gallery(body, cards)
        (root / page).write_text(_page(title, body, page, nav, note), encoding="utf-8")
    return root / "index.html"


def publish(
    out: Path,
    cache_root: Optional[Path] = None,
    on_progress: Callable[[str], None] = lambda text: None,
    source: Path = SOURCE,
) -> Path:
    """The docs, to publish, in `out`: docs/ and gallery/ (its page and the
    previews it shows), index.html going to docs/, .nojekyll. A folder
    not empty must be one this wrote before (SITE_MARK): its contents are
    replaced - but for a .git (a worktree of the branch Pages serves)."""
    theirs = [p for p in out.iterdir() if p.name != ".git"] if out.exists() else []
    if theirs and not (out / SITE_MARK).exists():
        raise KdenliveAsCodeError(msg("docs.public_not_empty", path=out))
    base = (cache_root or default_cache_root()) / PUBLIC_DIR
    first = build(cache_root=base, on_progress=on_progress, source=source, public=True)
    out.mkdir(parents=True, exist_ok=True)
    for path in theirs:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    shutil.copytree(first.parent, out / DOCS_DIR)
    gallery_root = base / gallery.GALLERY_DIR
    (out / gallery.GALLERY_DIR).mkdir()
    shutil.copy2(gallery_root / "index.html", out / gallery.GALLERY_DIR / "index.html")
    shutil.copytree(gallery_root / "files", out / gallery.GALLERY_DIR / "files")
    (out / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={DOCS_DIR}/index.html">'
        f'<link rel="canonical" href="{DOCS_DIR}/index.html"><a href="{DOCS_DIR}/index.html">video-as-code</a>\n',
        encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / SITE_MARK).write_text(msg("docs.site_mark"), encoding="utf-8")
    return out / "index.html"


def _page(title: str, body: str, current: str, nav: list[tuple[str, str]], note: str = "") -> str:
    links = "".join(
        f'<a href="{page}"{" aria-current=page" if page == current else ""}>{html.escape(name)}</a>' for page, name in nav
    )
    links += f'<a href="../{gallery.GALLERY_DIR}/index.html">{html.escape(msg("docs.gallery"))}</a>'
    return f"""<!doctype html>
<html lang="{msg("gallery.lang")}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - video-as-code</title>
<style>{_CSS}</style>
</head>
<body>
<nav>{links}</nav>
<main>
{body}
</main>
{f'<footer>{html.escape(note)}</footer>' if note else ""}
<script>{_JS}</script>
</body>
</html>
"""


_CSS = """
:root { color-scheme: light dark; --bg: #fdfdfc; --fg: #1d1f21; --muted: #6b7075; --line: #e2e4e6; --code: #f2f3f4; --accent: #2a6fdb; }
@media (prefers-color-scheme: dark) { :root { --bg: #16181a; --fg: #e4e6e8; --muted: #9aa0a6; --line: #2c3034; --code: #212427; --accent: #7aa7f0; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, sans-serif; }
nav { position: sticky; top: 0; display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 10px 16px; background: var(--bg);
      border-bottom: 1px solid var(--line); z-index: 1; }
nav a { color: var(--muted); text-decoration: none; }
nav a[aria-current], nav a:hover { color: var(--fg); }
main { max-width: 960px; margin: 0 auto; padding: 8px 16px 64px; }
a { color: var(--accent); }
code { font: 0.9em ui-monospace, monospace; background: var(--code); padding: 1px 4px; border-radius: 4px; }
pre { background: var(--code); padding: 12px; border-radius: 6px; overflow-x: auto; }
pre code { padding: 0; background: none; }
table { border-collapse: collapse; display: block; overflow-x: auto; margin: 12px 0; }
th, td { border: 1px solid var(--line); padding: 6px 10px; vertical-align: top; text-align: left; }
tr:target { background: color-mix(in srgb, var(--accent) 15%, transparent); }
a.to-gallery { text-decoration: none; font-size: 0.8em; margin-left: 4px; }
h1, h2, h3 { line-height: 1.25; }
footer { max-width: 960px; margin: 0 auto; padding: 16px; color: var(--muted); font-size: 14px; border-top: 1px solid var(--line); }
"""

_JS = """
// a link here to a row opens nothing more, but shows it: scrolled to, below the bar
if (location.hash) { const el = document.getElementById(location.hash.slice(1)); if (el) el.scrollIntoView({block: "center"}); }
"""
