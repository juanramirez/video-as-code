"""yamlschema.py: the project YAML's JSON Schema - shipped up to date,
in step with the parsers and the reference, accepting the project files
the docs and the gallery show and catching the mistakes it's for."""
from __future__ import annotations

import glob
import json
import re

import pytest
import yaml

from video_as_code import cli, gallery, yamlschema
from video_as_code.schema import RESERVED_TOP_LEVEL_KEYS

from test_docs_reference import ROOT, _parsed_keys

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads(yamlschema.shipped())
VALIDATOR = jsonschema.Draft7Validator(SCHEMA)

PROJECT = {"id": "p", "title": "T", "fps": 25, "width": 320, "height": 240}


def _errors(data: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in VALIDATOR.iter_errors(data)]


def _with_clip(**keys) -> dict:
    return {"project": PROJECT, "bin": [{"id": "a", "path": "a.png"}],
            "timeline": {"tracks": [{"type": "video", "clips": [{"clip": "a", "start": 0, **keys}]}]}}


def test_the_shipped_schema_is_up_to_date():
    built = yamlschema.dumps(yamlschema.build(yamlschema.REFERENCE.read_text(encoding="utf-8")))
    assert built == yamlschema.shipped(), "out of date: run `python -m video_as_code.yamlschema`"


def test_a_valid_draft_7_schema():
    jsonschema.Draft7Validator.check_schema(SCHEMA)


def _property_names(node) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        names |= set(node.get("properties", {})) if isinstance(node.get("properties"), dict) else set()
        for value in node.values():
            names |= _property_names(value)
    elif isinstance(node, list):
        for value in node:
            names |= _property_names(value)
    return names


def test_every_key_the_parsers_read_is_in_it():
    missing = sorted(_parsed_keys() - _property_names(SCHEMA))
    assert not missing, f"keys the parsers read that the schema doesn't know: {missing}"


def test_every_key_of_the_reference_is_described():
    tables = yamlschema.reference_tables(yamlschema.REFERENCE.read_text(encoding="utf-8"))
    where = {
        "`project:`": "project", "`project.sources:`": "sources", "`bin:`": "binClip", "Title cards (`type: title`)": "binClip",
        "`derive:`": "derive", "`timeline.tracks:`": "track", "Clips on a track": "clip", "`scenes:`": "scene",
        "`guides:`": "guide", "`transitions:`": "transition",
    }
    assert set(where) <= set(tables)
    for heading, name in where.items():
        props = SCHEMA["definitions"][name]["properties"]
        for key in tables[heading]:
            assert key in props, f"{heading}: {key}"
            assert props[key].get("description"), f"{heading}: {key} has no description"


def test_every_key_has_a_description():
    for name in ("project", "sources", "binClip", "derive", "track", "clip", "scene", "guide", "transition"):
        for key, prop in SCHEMA["definitions"][name]["properties"].items():
            assert prop.get("description") or key == "use", f"{name}.{key}"  # (use: the one it refers to has it)
    for key, prop in SCHEMA["properties"].items():
        assert prop.get("description") or key == "use", key
    assert SCHEMA["definitions"]["use"]["description"]
    assert set(SCHEMA["properties"]) >= RESERVED_TOP_LEVEL_KEYS


def _examples():
    """(where, YAML) of every whole project file the docs and the gallery show."""
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for i, block in enumerate(re.findall(r"```yaml\n(.*?)```", path.read_text(encoding="utf-8"), re.S)):
            yield f"{path.name}#{i}", block
    for path in sorted(glob.glob(str(ROOT / "examples" / "*" / "*.yaml"))):
        yield path, open(path, encoding="utf-8").read()
    entries = [*gallery.CATALOG, *gallery.lut_entries(), *gallery.glsl_entries(), *gallery.gl_entries()]
    for entry in entries:
        # its samples' paths, as render puts them in
        yield f"gallery: {entry.section}/{entry.name}", re.sub(r"\{(\w+)\}", r"samples/\1", entry.project)


def test_the_docs_and_the_gallery_projects_follow_it():
    checked = 0
    for where, text in _examples():
        data = yaml.safe_load(text)
        project = data.get("project") if isinstance(data, dict) else None
        # (excerpts left out: a bit of a map, or `project: {...}`)
        if not isinstance(data, dict) or set(data) - set(SCHEMA["properties"]) or (
                "project" in data and not (isinstance(project, dict) and "id" in project)):
            continue
        assert not _errors(data), f"{where}: {_errors(data)}"
        checked += 1
    assert checked > 100


@pytest.mark.parametrize("keys, wrong", [
    ({"fade_inn": 1}, "fade_inn"),                      # a typo
    ({"reveal": "irs"}, "reveal"),                       # not one of the values
    ({"chroma_key_distance": 2}, "chroma_key_distance"),  # out of range
    ({"overlay_color": [255, 255, 255]}, "overlay_color"),
    ({"film_grain": {"noise": 12, "contrst": 100}}, "film_grain"),
    ({"glsl": {"shader": "crt", "params": {"curvature": 0.2, "scanline": 1}}}, "glsl"),
    ({"glsl": {"shader": "crt", "params": {"curvature": 5}}}, "glsl"),
])
def test_a_wrong_clip_key_is_caught(keys, wrong):
    errors = _errors(_with_clip(**keys))
    assert errors and any(wrong in e for e in errors), errors


def test_the_right_ones_pass():
    assert not _errors(_with_clip(fade_in=1, reveal="iris", glsl={"shader": "crt", "params": {"curvature": 0.2}},
                                  film_grain={"noise": 12}, melt=["frei0r.cartoon", {"avfilter.hue": {"av.h": 3}}]))
    assert not _errors(_with_clip(glsl="mine.glsl"))


def test_a_transition_knows_its_gl_params():
    def scene(**keys):
        return {"project": PROJECT, "scenes": [{"clips": ["a.png", "b.png"], "transition": "gl", **keys}]}

    assert not _errors(scene(gl="angular", gl_params={"startingAngle": 45}))
    assert _errors(scene(gl="angular", gl_params={"startAngle": 45}))  # not its name
    assert _errors(scene(gl="angular", gl_params={"startingAngle": 400}))  # past its max
    assert _errors(scene(gl="angulr"))
    assert not _errors(scene(transition="wipe", luma="openshot/fade_in"))
    assert _errors(scene(transition="wipe", luma="radiall"))
    assert not _errors(scene(transition="melt/composite", transition_properties={"halign": "centre"}))


def test_use_may_bring_the_required_keys():
    data = {"project": PROJECT, "presets": {"still": {"duration": 2}},
            "timeline": {"tracks": [{"type": "video", "clips": [{"use": "still", "clip": "a", "start": 0}]}]},
            "bin": [{"use": "local"}]}
    assert not _errors(data)
    assert _errors({**data, "bin": [{"path": "a.png"}]})  # no id, nor a use: that could bring it


def test_a_part_file_and_its_entries():
    assert not _errors({"bin": [{"id": "a", "path": "a.png"}], "scenes": [{"clips": [{"clip": "a"}]}]})
    assert not _errors({"project": PROJECT, "parts": ["intro.yaml", {"file": "b.yaml", "transition": "gl", "gl": "cube"}]})
    assert _errors({"project": PROJECT, "parts": [{"file": "b.yaml", "transition": "glitch"}]})


def test_the_command_writes_it(tmp_path, capsys):
    out = tmp_path / "schema.json"
    assert cli.main(["schema", "-o", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == SCHEMA
    assert f"$schema={out}" in capsys.readouterr().out
    assert cli.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out) == SCHEMA
