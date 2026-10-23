"""What the docs' pages and the gallery share when published together
(docs.py): the bar at the top, its colours, and the move between them."""
from __future__ import annotations

import html
from typing import Optional

SITE_CSS = """
:root { color-scheme: light dark; --bg: #fdfdfc; --fg: #1d1f21; --muted: #6b7075; --line: #e2e4e6; --code: #f2f3f4; }
@media (prefers-color-scheme: dark) { :root { --bg: #16181a; --fg: #e4e6e8; --muted: #9aa0a6; --line: #2c3034; --code: #212427; } }
/* from one page to another: a cross-fade, the bar staying put (where a
   browser can't, it just loads the page) */
@view-transition { navigation: auto; }
nav.site { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 10px 16px;
           background: var(--bg); border-bottom: 1px solid var(--line); font: 16px/1.6 system-ui, sans-serif;
           view-transition-name: site-nav; }
nav.site a { color: var(--muted); text-decoration: none; }
nav.site a[aria-current], nav.site a:hover { color: var(--fg); }
nav.site a:first-child { color: var(--fg); font-weight: 600; }  /* the home: the site's name */
nav.site a.repo { margin-left: auto; }
"""


def nav(pages: list[tuple[str, str]], current: str, here: str, repo: Optional[str] = None) -> str:
    """The bar: `pages` as (path from the site's root, name), `current`
    one of those paths, `here` the folder the page is in."""
    def href(path: str) -> str:
        folder, _, name = path.rpartition("/")
        return name if folder == here else f"../{path}"

    links = "".join(
        f'<a href="{html.escape(href(path))}"{" aria-current=page" if path == current else ""}>{html.escape(name)}</a>'
        for path, name in pages
    )
    if repo:
        links += f'<a class="repo" href="{html.escape(repo)}">GitHub</a>'
    return f'<nav class="site">{links}</nav>'
