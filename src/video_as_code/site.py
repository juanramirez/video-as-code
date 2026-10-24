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
/* the pages wrap among themselves; GitHub stays on the right, centred
   on however many lines they take */
nav.site { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 18px; padding: 10px 16px;
           background: var(--bg); border-bottom: 1px solid var(--line); font: 16px/1.6 system-ui, sans-serif;
           view-transition-name: site-nav; }
nav.site .pages { flex: 1; display: flex; flex-wrap: wrap; gap: 4px 18px; }
nav.site a { color: var(--muted); text-decoration: none; }
nav.site a[aria-current], nav.site a:hover { color: var(--fg); }
nav.site a:first-child { color: var(--fg); font-weight: 600; }  /* the home: the site's name */
nav.site a.repo { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
nav.site a.repo svg { width: 1em; height: 1em; fill: currentColor; }
/* on a phone the bar takes several lines: it scrolls away with the page
   instead of covering it, and tightens up */
@media (max-width: 640px) {
  nav.site { position: static; font-size: 15px; line-height: 1.4; gap: 12px; padding: 8px 16px; }
  nav.site .pages { gap: 4px 14px; }
}
"""


# GitHub's mark (Octicons' mark-github, MIT)
GITHUB_MARK = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17'
    '-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08'
    '-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51'
    '.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67'
    '.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0'
    '-4.42 3.58-8 8-8Z"/></svg>'
)


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
    repo_link = f'<a class="repo" href="{html.escape(repo)}">{GITHUB_MARK}GitHub</a>' if repo else ""
    return f'<nav class="site"><span class="pages">{links}</span>{repo_link}</nav>'
