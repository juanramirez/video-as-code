"""A project's album by NAME, before it exists: `project.sources.album`
can be a map,

    sources:
      album:
        name: "vid · summer · 2014"
        url: https://photos.app.goo.gl/...

where `name` is what to call the album and `url` its share-by-link link.
With a name and no url yet, `fetch` can't get anything from it and says so
(fetch.album_not_created); a tool that creates the album - in Google
Photos, say, by adding the clips it found to a new album and sharing it -
then calls `write_album_url` to put the link in the YAML, and from there
on it works like a plain `album: <link>`. The album stays: `fetch` needs
it every time a file is missing (another machine, a pruned file).

Creating the album is up to that tool (a photo service's API or its web
page); this module only knows the YAML side.

The link goes in as a text edit of the lines involved, never parse +
re-dump (which would lose every comment and the file's own formatting) -
same as fetch_resolve.py - and the result is checked by parsing it: an
edit that didn't come out as intended (a layout this doesn't know) leaves
the file untouched.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from .fetch_resolve import _split_comment


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _child(lines: list[str], start: int, end: int, key: str) -> Optional[int]:
    """The line of `key:` directly under the block lines[start:end]
    (the first non-blank, non-comment line sets the children's indent)."""
    child_indent = None
    for i in range(start, end):
        code = _split_comment(lines[i])[0]
        if not code.strip():
            continue
        if child_indent is None:
            child_indent = _indent(code)
        if _indent(code) < child_indent:
            return None
        if _indent(code) == child_indent and re.match(rf"\s*{re.escape(key)}:(\s|$)", code):
            return i
    return None


def _block_end(lines: list[str], i: int) -> int:
    """The line after the block that starts at lines[i] (its key line plus
    everything indented deeper)."""
    own = _indent(lines[i])
    j = i + 1
    while j < len(lines):
        code = _split_comment(lines[j])[0]
        if code.strip() and _indent(code) <= own:
            break
        j += 1
    return j


def set_album_url(text: str, url: str) -> Optional[str]:
    """`text` (a project YAML) with `url` as project.sources.album's link,
    keeping its name - or None if the album isn't written in a way this
    knows (then nothing should be written: tell the user to add it by hand)."""
    lines = text.splitlines(keepends=True)
    project = next((i for i, line in enumerate(lines) if re.match(r"project:(\s|$)", line)), None)
    if project is None:
        return None
    sources = _child(lines, project + 1, _block_end(lines, project), "sources")
    if sources is None:
        return None
    album = _child(lines, sources + 1, _block_end(lines, sources), "album")
    if album is None:
        return None
    code, comment = _split_comment(lines[album])
    newline = "\n" if lines[album].endswith("\n") else ""
    comment = comment.rstrip("\n")
    head, _, value = code.rstrip("\n").partition("album:")
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        # flow map: {name: x} -> {name: x, url: 'link'}; an existing url is replaced
        inner = re.sub(r",?\s*url:\s*(\"[^\"]*\"|'[^']*'|[^,}]*)", "", value[1:-1]).strip().strip(",").strip()
        parts = ([inner] if inner else []) + [f"url: '{url}'"]
        lines[album] = f"{head}album: {{{', '.join(parts)}}}" + (f" {comment.strip()}" if comment else "") + newline
    elif value:
        # a plain link: replaced
        lines[album] = f"{head}album: {url}" + (f" {comment.strip()}" if comment else "") + newline
    else:
        # block map: replace its url: line, or add one after its last line
        end = _block_end(lines, album)
        existing = _child(lines, album + 1, end, "url")
        if existing is not None:
            c_code, c_comment = _split_comment(lines[existing])
            nl = "\n" if lines[existing].endswith("\n") else ""
            pad = c_code[: _indent(c_code)]
            lines[existing] = f"{pad}url: {url}" + (f" {c_comment.strip()}" if c_comment.strip() else "") + nl
        else:
            name = _child(lines, album + 1, end, "name")
            if name is None:
                return None
            pad = lines[name][: _indent(lines[name])]
            last = end - 1
            while last > album and not _split_comment(lines[last])[0].strip():
                last -= 1  # don't put it after trailing blank or comment lines
            if not lines[last].endswith("\n"):
                lines[last] += "\n"
            lines.insert(last + 1, f"{pad}url: {url}\n")
    new = "".join(lines)
    return new if _album_of(new) == {**(_album_of(text) or {}), "url": url} else None


def _album_of(text: str) -> Optional[dict]:
    """project.sources.album as a {name?, url?} map, or None."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    album = ((data or {}).get("project") or {}).get("sources", {}).get("album") if isinstance(data, dict) else None
    if isinstance(album, dict):
        return {k: v for k, v in album.items() if k in ("name", "url")}
    return {"url": album} if album else {}


def write_album_url(yaml_path: Path, url: str) -> bool:
    """Puts `url` in `yaml_path` as its album's link (see set_album_url);
    False, with the file untouched, if it couldn't be done."""
    new = set_album_url(yaml_path.read_text(encoding="utf-8"), url)
    if new is None:
        return False
    yaml_path.write_text(new, encoding="utf-8")
    return True
