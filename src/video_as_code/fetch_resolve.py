"""Interactive fixing of what `fetch` couldn't get (fetch.Problem): once
everything else is downloaded, the CLI walks through each problem and
lets the user decide there and then -

  r) retry it as is (a network hiccup, or it's just been added to the album)
  u) give a link to download it from instead (Drive, YouTube, ...)
  a) name another album item (it's there under a different original name)
  l) use a file on this machine (only here: other machines still lack it)
  s) skip it

- and `fetch` then runs again, only for what's still missing.

`u` and `a` WRITE THE FIX INTO THE PROJECT YAML, so the next fetch (here
or anywhere) finds it. That's done as a text edit of the lines involved
(never parse + re-dump, which would lose every comment and the file's
own formatting), and checked by parsing the result: if the edit didn't
come out as intended - an unusual layout this doesn't know - the file is
left untouched and the user is told to make the change by hand.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from . import fetch
from .i18n import msg

# Links that only work for their owner: an item of someone's Google Photos
# LIBRARY (not of the shared album) can't be downloaded anonymously.
_PRIVATE_PHOTOS_HOSTS = {"photos.google.com", "photos.app.goo.gl"}

_BIN_START_RE = re.compile(r"^bin:\s*(#.*)?$")
_TOP_LEVEL_RE = re.compile(r"^[^\s#-]")
# a bin entry's first line, block (`  - id: x`) or flow (`  - {id: x, ...}`)
_ENTRY_RE = re.compile(r"""^(?P<indent>\s*)-\s+(?:\{\s*)?id:\s*(?P<q>["']?)(?P<id>[^"',}\s#]+)(?P=q)""")


@dataclass
class Outcome:
    changed: bool = False  # anything done that's worth fetching again for
    skipped: set[str] = field(default_factory=set)  # problem keys the user skipped


def key(problem: fetch.Problem) -> str:
    return problem.source if problem.source is not None else f"url:{problem.link_clip}"


# --- YAML text edits ----------------------------------------------------------


def _bin_range(lines: list[str]) -> tuple[int, int]:
    start = next((i for i, line in enumerate(lines) if _BIN_START_RE.match(line)), None)
    if start is None:
        return 0, 0
    end = next((i for i in range(start + 1, len(lines)) if _TOP_LEVEL_RE.match(lines[i])), len(lines))
    return start + 1, end


def _entry_range(lines: list[str], clip_id: str) -> Optional[tuple[int, int]]:
    """[first, last+1) lines of bin entry `clip_id` (its nested lines included)."""
    first, end = _bin_range(lines)
    for i in range(first, end):
        match = _ENTRY_RE.match(lines[i])
        if not match or match.group("id") != clip_id:
            continue
        indent = len(match.group("indent"))
        j = i + 1
        while j < end:
            stripped = lines[j].strip()
            if stripped and not stripped.startswith("#") and len(lines[j]) - len(lines[j].lstrip()) <= indent:
                break
            j += 1
        return i, j
    return None


def _split_comment(line: str) -> tuple[str, str]:
    """(code, comment) - a `#` starts a comment only after whitespace or at
    the line's start (links can contain `#`)."""
    match = re.search(r"(^|\s)#", line)
    return (line, "") if not match else (line[: match.start()], line[match.start():])


def _source_re(name: str) -> re.Pattern:
    return re.compile(r"(?P<key>\bsource:\s*)(?P<q>[\"']?)" + re.escape(name) + r"(?P=q)(?=\s*(,|\}|$))")


def _in_flow(code: str, pos: int) -> bool:
    return code.rfind("{", 0, pos) > code.rfind("}", 0, pos)


def replace_source(text: str, old: str, new: str) -> str:
    """Every `source: <old>` of the bin (plain clips and derive: inputs
    alike: it's the same album item) becomes `source: <new>`."""
    lines = text.splitlines(keepends=True)
    first, end = _bin_range(lines)
    pattern = _source_re(old)
    for i in range(first, end):
        code, comment = _split_comment(lines[i])
        lines[i] = pattern.sub(lambda m: f"{m.group('key')}{m.group('q')}{new}{m.group('q')}", code) + comment
    return "".join(lines)


def source_to_url(text: str, clip_ids: list[str], old: str, link: str) -> str:
    """In each of `clip_ids`' entries, `source: <old>` becomes `url: <link>`."""
    lines = text.splitlines(keepends=True)
    pattern = _source_re(old)
    for clip_id in clip_ids:
        span = _entry_range(lines, clip_id)
        if span is None:
            continue
        for i in range(*span):
            code, comment = _split_comment(lines[i])
            match = pattern.search(code)
            if match:
                value = f"'{link}'" if _in_flow(code, match.start()) else link
                lines[i] = code[: match.start()] + f"url: {value}" + code[match.end():] + comment
    return "".join(lines)


def add_link(text: str, clip_id: str, link: str) -> str:
    """Puts `link` FIRST in `clip_id`'s `url:` (tried before the others),
    whichever way it's written: a single link (becomes a block list), a
    block list, or a flow list."""
    lines = text.splitlines(keepends=True)
    span = _entry_range(lines, clip_id)
    if span is None:
        return text
    for i in range(*span):
        code, comment = _split_comment(lines[i])
        match = re.search(r"\burl:\s*", code)
        if not match:
            continue
        rest = code[match.end():]
        newline = "\n" if lines[i].endswith("\n") else ""
        rest_stripped = rest.rstrip("\n")
        if rest_stripped.lstrip().startswith("["):  # flow list
            at = match.end() + rest.index("[") + 1
            lines[i] = code[:at] + f"'{link}', " + code[at:] + comment
        elif not rest_stripped.strip():  # block list: the items follow
            item = next((j for j in range(i + 1, span[1]) if lines[j].lstrip().startswith("- ")), None)
            if item is None:
                return text
            item_indent = lines[item][: len(lines[item]) - len(lines[item].lstrip())]
            lines.insert(item, f"{item_indent}- {link}\n")
        elif _in_flow(code, match.start()):  # `{id: x, url: 'a'}`
            value = re.match(r"(['\"]?)[^,}\s]+\1", rest)
            if value is None:
                return text
            at = match.end()
            lines[i] = code[:at] + f"['{link}', {value.group(0)}]" + code[at + value.end():] + comment
        else:  # `url: a` on its own line
            indent = code[: len(code) - len(code.lstrip())] + "  "
            old_value = rest_stripped.strip()
            trailing = (" " + comment.strip()) if comment.strip() else ""
            lines[i] = (
                code[: match.start()] + "url:" + "\n" + f"{indent}- {link}\n" + f"{indent}- {old_value}{trailing}" + newline
            )
        return "".join(lines)
    return text


def _bin_entry(text: str, clip_id: str) -> Optional[dict]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    for entry in (data or {}).get("bin") or []:
        if isinstance(entry, dict) and str(entry.get("id")) == clip_id:
            return entry
    return None


def _write_checked(yaml_path: Path, text: str, check: Callable[[str], bool]) -> bool:
    if not check(text):
        return False
    yaml_path.write_text(text, encoding="utf-8")
    return True


# --- the dialogue -------------------------------------------------------------


def resolve(
    yaml_path: Path,
    problems: list[fetch.Problem],
    ask: Optional[Callable[[str], str]] = None,
    say: Optional[Callable[[str], None]] = None,
) -> Outcome:
    """Asks what to do about each problem and does it. Ctrl-D skips the
    rest. `ask`/`say` default to input()/print() (looked up at call time)."""
    ask = ask or (lambda prompt: input(prompt))
    say = say or print
    outcome = Outcome()
    say(msg("resolve.intro", n=len(problems)))
    for n, problem in enumerate(problems, start=1):
        say("")
        say(f"[{n}/{len(problems)}] {problem.reason}")
        try:
            done = _resolve_one(yaml_path, problem, ask, say)
        except EOFError:
            outcome.skipped |= {key(p) for p in problems[n - 1:]}
            break
        if done:
            outcome.changed = True
        else:
            outcome.skipped.add(key(problem))
    return outcome


def _resolve_one(yaml_path: Path, problem: fetch.Problem, ask, say) -> bool:
    """True once something was done about `problem`, False if skipped."""
    plain_ids = [c for c in problem.clip_ids if c not in problem.derive_ids]
    options = ["r"]
    if problem.link_clip is not None or plain_ids:
        options.append("u")
    if problem.source is not None:
        options.append("a")
    options += ["l", "s"]
    for option in options:
        say(f"  {option}) {msg('resolve.option_' + option)}")
    if problem.source is not None and problem.derive_ids and plain_ids:
        say(msg("resolve.link_not_for_derive", ids=", ".join(problem.derive_ids)))
    while True:
        choice = ask(msg("resolve.choose", options="/".join(options))).strip().lower()
        if choice not in options:
            continue
        if choice == "s":
            return False
        if choice == "r":
            return True
        if choice == "u" and _give_link(yaml_path, problem, plain_ids, ask, say):
            return True
        if choice == "a" and _rename_source(yaml_path, problem, ask, say):
            return True
        if choice == "l" and _use_local(yaml_path, problem, ask, say):
            return True


def _give_link(yaml_path: Path, problem: fetch.Problem, plain_ids: list[str], ask, say) -> bool:
    link = ask(msg("resolve.ask_link")).strip()
    if not link:
        return False
    parsed = urllib.parse.urlparse(link)
    if parsed.scheme not in ("http", "https"):
        say(msg("resolve.not_a_link"))
        return False
    if (parsed.hostname or "").lower() in _PRIVATE_PHOTOS_HOSTS:
        say(msg("resolve.private_photos_link"))
        return False
    text = yaml_path.read_text(encoding="utf-8")
    if problem.link_clip is not None:
        new = add_link(text, problem.link_clip, link)
        targets = [problem.link_clip]
    else:
        new = source_to_url(text, plain_ids, problem.source, link)
        targets = plain_ids

    def check(candidate: str) -> bool:
        for clip_id in targets:
            entry = _bin_entry(candidate, clip_id) or {}
            urls = entry.get("url")
            urls = urls if isinstance(urls, list) else [urls]
            if not urls or urls[0] != link or entry.get("source") is not None:
                return False
        return True

    if not _write_checked(yaml_path, new, check):
        say(msg("resolve.edit_failed", path=yaml_path))
        return False
    say(msg("resolve.yaml_updated", path=yaml_path.name))
    return True


def _rename_source(yaml_path: Path, problem: fetch.Problem, ask, say) -> bool:
    name = ask(msg("resolve.ask_name")).strip()
    if not name:
        return False
    if Path(name).name != name or name in (".", ".."):
        say(msg("resolve.not_a_name"))
        return False
    text = yaml_path.read_text(encoding="utf-8")
    new = replace_source(text, problem.source, name)

    def check(candidate: str) -> bool:
        for clip_id in problem.clip_ids:
            entry = _bin_entry(candidate, clip_id) or {}
            derive = entry.get("derive") if isinstance(entry.get("derive"), dict) else {}
            if name not in (entry.get("source"), derive.get("source")):
                return False
        return True

    if not _write_checked(yaml_path, new, check):
        say(msg("resolve.edit_failed", path=yaml_path))
        return False
    say(msg("resolve.yaml_updated", path=yaml_path.name))
    return True


def _use_local(yaml_path: Path, problem: fetch.Problem, ask, say) -> bool:
    raw = ask(msg("resolve.ask_file")).strip().strip("'\"")  # a path dragged into a terminal comes quoted
    if not raw:
        return False
    path = Path(raw).expanduser()
    if not path.is_file():
        say(msg("resolve.no_such_file", path=path))
        return False
    dest = fetch.use_local_file(yaml_path, problem, path)
    say(msg("resolve.copied_locally", path=dest))
    return True
