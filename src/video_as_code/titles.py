"""Generates `.kdenlivetitle` files - the small standalone XML format
kdenlive's title clips point to (`mlt_service: kdenlivetitle`, `resource:
<path to the .kdenlivetitle file>`), separate from the main `.kdenlive`
project XML the rest of this generator writes.

Modeled directly on a real title file from an actual kdenlive project
(not built by analogy): a single `QGraphicsTextItem`, a `startviewport`/
`endviewport` pair (identical here - no position/size animation, which
this generator doesn't support), and a `background`. Scope is
deliberately narrow: one text item, one font/color/background, no
multiple items, no image/rect items, no animation - a title card or
caption, not a replacement for kdenlive's title editor. Multi-line text
(literal `\n` in the string) IS supported within that single item - see
below - just not multiple independently-styled/positioned items.

Text centering and sizing are an approximation, not exact metrics:
without a font rendering library as a dependency, there's no way to
measure the real pixel width of a string in a given font before MLT
itself renders it. The text box is sized/centered from a rough
average-glyph-width estimate, and `font_size` is scaled down first if
that estimate would overflow the canvas (checked empirically: an
unclamped box let long text run off the edge, unreadable, with the
earlier version of this function). Looks right for normal caption-length
text; unusual fonts or many short lines can still drift - a known,
documented limitation, not a bug.

Multi-line text: a literal `\n` in `text` renders as multiple visually
separated lines - confirmed against the real rendering path, not
assumed. MLT's `kdenlivetitle` producer (`~/dev/mlt/src/modules/qt/
kdenlivetitle_wrapper.cpp`, `PlainTextItem::updateText`) splits the raw
`<content>` text node on `\n` itself (`text.split('\n')`) and lays out
each line independently using the *real* font's own `QFontMetrics` line
spacing - not this module's rough per-line-height estimate. That
estimate only drives `box-width`/`box-height`/the item's `<position>`
(so the block starts out reasonably centered); MLT's own renderer
auto-expands its internal bounding rect if the actual text needs more
room than the estimate gave it (`m_lineSpacing * lines.size()`), so an
imprecise height estimate can't garble or overlap lines - at worst it
leaves the block slightly off-center vertically, same class of
imprecision the width/centering heuristic already accepts for single
lines.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# Qt::AlignHCenter | Qt::AlignVCenter
ALIGNMENT_CENTER = 4 | 128

# Rough average glyph-width-to-font-size ratio for a typical sans-serif
# font, used only to size/center the text box (see module docstring) -
# verified by eye against real renders, not derived from font metrics.
_AVG_CHAR_WIDTH_FACTOR = 0.55


def render_title_xml(
    text: str,
    *,
    width: int,
    height: int,
    duration_frames: int,
    font: str = "Sans",
    font_size: int = 48,
    font_color: tuple[int, int, int, int] = (255, 255, 255, 255),
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
    font_weight: int = 400,
    italic: bool = False,
    outline: float = 0.0,
    outline_color: tuple[int, int, int, int] = (0, 0, 0, 255),
    shadow: Optional[tuple[tuple[int, int, int, int], int, int, int]] = None,
) -> str:
    def rgba(c: tuple[int, int, int, int]) -> str:
        return ",".join(str(v) for v in c)

    root = ET.Element(
        "kdenlivetitle",
        {
            "duration": str(duration_frames),
            "LC_NUMERIC": "C",
            "width": str(width),
            "height": str(height),
            "out": str(duration_frames),
        },
    )
    item = ET.SubElement(root, "item", {"type": "QGraphicsTextItem", "z-index": "0"})
    margin = round(width * 0.05)
    max_text_width = max(width - 2 * margin, 1)
    lines = text.split("\n")
    longest_line = max(lines, key=len)
    estimated_width = len(longest_line) * font_size * _AVG_CHAR_WIDTH_FACTOR
    if estimated_width > max_text_width:
        font_size = max(1, round(font_size * max_text_width / estimated_width))
        estimated_width = max_text_width
    box_width = min(width, round(estimated_width))
    # font_size * 2 for a single line (generous ascent/descent padding,
    # unchanged from before multi-line support existed) plus one more
    # font_size per additional line - a rough estimate only, see the
    # module docstring for why MLT's own renderer doesn't actually need
    # this to be exact.
    box_height = font_size * (1 + len(lines))
    x = (width - box_width) // 2
    y = (height - box_height) // 2
    position = ET.SubElement(item, "position", {"x": str(x), "y": str(y)})
    ET.SubElement(position, "transform").text = "1,0,0,0,1,0,0,0,1"
    content = ET.SubElement(
        item,
        "content",
        {
            "font": font,
            "font-pixel-size": str(font_size),
            "font-italic": "1" if italic else "0",
            "font-underline": "0",
            # Qt 6's scale (kdenlive writes QFont::weight(), 400 normal);
            # Qt 5's 50 rendered the same, but a font with a Thin face
            # could pick it, and kdenlive's title editor would show it so
            "font-weight": str(font_weight),
            "font-color": rgba(font_color),
            "font-outline": format(outline, "g"),
            "font-outline-color": rgba(outline_color),
            "box-width": str(box_width),
            "box-height": str(box_height),
            "alignment": str(ALIGNMENT_CENTER),
            "letter-spacing": "0",
        },
    )
    if shadow is not None:
        # kdenlive's own form (MyTextItem::shadowInfo), which MLT's
        # kdenlivetitle reads: enabled;#AARRGGBB;blur;x;y
        (r, g, b, a), blur, dx, dy = shadow
        content.set("shadow", f"1;#{a:02x}{r:02x}{g:02x}{b:02x};{blur};{dx};{dy}")
    content.text = text
    ET.SubElement(root, "startviewport", {"rect": f"0,0,{width},{height}"})
    ET.SubElement(root, "endviewport", {"rect": f"0,0,{width},{height}"})
    ET.SubElement(root, "background", {"color": rgba(background)})
    return ET.tostring(root, encoding="unicode")


def write_title_file(path: Path, xml_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml_text, encoding="utf-8")


def parse_title_xml(xml_text: str) -> Optional[dict]:
    """The inverse of `render_title_xml`, for `reverse.py` - recognizes
    only the same narrow shape this module generates (single static
    `QGraphicsTextItem`, `startviewport` == `endviewport`). Anything
    richer (multiple items, image/rect items, animation) returns None so
    the caller falls back to `x_kdenlive_raw` instead of guessing at or
    silently dropping content this module can't actually represent."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "kdenlivetitle":
        return None

    items = root.findall("item")
    if len(items) != 1 or items[0].get("type") != "QGraphicsTextItem":
        return None

    start_vp = root.find("startviewport")
    end_vp = root.find("endviewport")
    if start_vp is None or end_vp is None or start_vp.get("rect") != end_vp.get("rect"):
        return None

    content = items[0].find("content")
    if content is None or content.text is None:
        return None

    def parse_rgba(s: Optional[str]) -> Optional[list[int]]:
        if not s:
            return None
        parts = s.split(",")
        if len(parts) != 4:
            return None
        try:
            return [int(p) for p in parts]
        except ValueError:
            return None

    background_el = root.find("background")
    background = parse_rgba(background_el.get("color") if background_el is not None else None)
    font_color = parse_rgba(content.get("font-color"))
    try:
        font_size = round(float(content.get("font-pixel-size", 48)))
    except ValueError:
        font_size = 48

    title = {
        "text": content.text,
        "font": content.get("font", "Sans"),
        "font_size": font_size,
        "color": font_color or [255, 255, 255, 255],
        "background": background or [0, 0, 0, 0],
    }
    # the rest only when not the default
    try:
        weight = int(content.get("font-weight", 400))
    except ValueError:
        weight = 400
    if weight < 100:  # Qt 5's scale (50 normal, 75 bold), as older files have it
        weight = {50: 400, 75: 700}.get(weight, max(100, min(900, round(weight / 99 * 900 / 100) * 100)))
    if weight != 400:
        title["font_weight"] = weight
    if content.get("font-italic", "0") not in ("", "0"):
        title["italic"] = True
    try:
        outline = float(content.get("font-outline", 0))
    except ValueError:
        outline = 0.0
    if outline:
        title["outline"] = outline
        title["outline_color"] = parse_rgba(content.get("font-outline-color")) or [0, 0, 0, 255]
    shadow = (content.get("shadow") or "").split(";")
    if len(shadow) == 5 and shadow[0] not in ("", "0") and shadow[1].startswith("#") and len(shadow[1]) == 9:
        try:
            argb = int(shadow[1][1:], 16)
            title["shadow"] = {
                "color": [(argb >> 16) & 255, (argb >> 8) & 255, argb & 255, (argb >> 24) & 255],
                "blur": int(shadow[2]),
                "offset": [int(shadow[3]), int(shadow[4])],
            }
        except ValueError:
            pass
    return title
