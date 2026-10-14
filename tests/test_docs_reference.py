"""docs/project-yaml.md stays complete: every key the YAML parsers read
(schema.py, scenes.py) must appear in it as `key`. Found by reading the
parsers' source, so a new option fails here until it's documented."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "src" / "video_as_code" / "schema.py", ROOT / "src" / "video_as_code" / "scenes.py"]
REFERENCE = ROOT / "docs" / "project-yaml.md"

_PATTERNS = [
    # item.get("key") / data["key"] / sources.get("key")... on the YAML maps
    r'\b(?:item|data|sources|scene|raw|kf|crop_raw|timeline_data)\.get\("(\w+)"',
    r'\b(?:item|data|scene|raw|kf)\["(\w+)"\]',
    # helpers that read item[key] for the key they're given
    r'\b_?(?:pair|quad)\("(\w+)"\)',
]


def _parsed_keys() -> set[str]:
    keys: set[str] = set()
    for source in SOURCES:
        text = source.read_text(encoding="utf-8")
        for pattern in _PATTERNS:
            keys |= set(re.findall(pattern, text))
        # sets/tuples of accepted keys: derive's `known`, the top level,
        # the old-film toggles read in a loop, color_grade's groups
        for block in re.findall(r"(?:known|RESERVED_TOP_LEVEL_KEYS) = \{([^}]*)\}", text):
            keys |= set(re.findall(r'"(\w+)"', block))
        for block in re.findall(r"for field_name in \(([^)]*)\)", text):
            keys |= set(re.findall(r'"(\w+)"', block))
        for block in re.findall(r"if key not in \(([^)]*)\)", text):
            keys |= set(re.findall(r'"(\w+)"', block))
    return keys


def test_every_parsed_key_is_in_the_reference():
    reference = REFERENCE.read_text(encoding="utf-8")
    keys = _parsed_keys()
    assert len(keys) > 80  # the patterns still find the parsers' keys
    # (written `key` in tables, `key:` in headings and examples)
    missing = sorted(k for k in keys if f"`{k}`" not in reference and f"`{k}:" not in reference)
    assert not missing, f"keys missing from {REFERENCE.name}: {missing}"
