"""albums.py: the album's link written into the YAML as a text edit, so
comments and layout survive; anything it can't do leaves the file alone."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from video_as_code import albums

URL = "https://photos.app.goo.gl/NEW"


def _album(text: str):
    return yaml.safe_load(text)["project"]["sources"]["album"]


BLOCK = """\
# a project
project:
  id: p1
  title: T
  sources:
    # the album for this video
    album:
      name: "vid · summer · 2014"  # created by the rebuild tool
    dir: assets
bin: []
"""


def test_block_map_gets_a_url_line_and_keeps_everything_else():
    new = albums.set_album_url(BLOCK, URL)
    assert _album(new) == {"name": "vid · summer · 2014", "url": URL}
    assert new == BLOCK.replace(
        '      name: "vid · summer · 2014"  # created by the rebuild tool\n',
        f'      name: "vid · summer · 2014"  # created by the rebuild tool\n      url: {URL}\n',
    )


def test_block_map_with_a_url_has_it_replaced():
    text = BLOCK.replace("tool\n", "tool\n      url: https://old  # old link\n")
    new = albums.set_album_url(text, URL)
    assert _album(new) == {"name": "vid · summer · 2014", "url": URL}
    assert "# old link" in new and "https://old" not in new


def test_block_map_with_trailing_comments_puts_url_after_the_last_key():
    text = BLOCK.replace("    dir: assets\n", "      # nothing else\n\n    dir: assets\n")
    new = albums.set_album_url(text, URL)
    assert _album(new)["url"] == URL
    assert yaml.safe_load(new)["project"]["sources"]["dir"] == "assets"


@pytest.mark.parametrize(
    "album, expected",
    [
        ("{name: 'vid · summer · 2014'}", {"name": "vid · summer · 2014", "url": URL}),
        ("{name: 'vid · summer · 2014', url: 'https://old'}", {"name": "vid · summer · 2014", "url": URL}),
        ("https://old  # keep this", URL),
    ],
)
def test_flow_map_and_plain_link(album: str, expected):
    text = f"project:\n  id: p1\n  sources:\n    album: {album}\n"
    new = albums.set_album_url(text, URL)
    assert _album(new) == expected
    if "keep this" in album:
        assert "# keep this" in new


@pytest.mark.parametrize(
    "text",
    [
        "project:\n  id: p1\n",  # no sources
        "project:\n  sources:\n    dir: assets\n",  # no album
        "project:\n  sources:\n    album:\n      title: x\n",  # neither name nor url under it
        "bin: []\n",  # no project
        "project: {id: p1, sources: {album: {name: x}}}\n",  # all in flow style
    ],
)
def test_layouts_it_doesnt_know_give_none(text: str):
    assert albums.set_album_url(text, URL) is None


def test_an_album_key_elsewhere_isnt_touched():
    text = "project:\n  sources:\n    album:\n      name: x\nbin:\n  - id: c\n    album: y\n"
    new = albums.set_album_url(text, URL)
    assert _album(new) == {"name": "x", "url": URL}
    assert yaml.safe_load(new)["bin"][0]["album"] == "y"


def test_write_album_url(tmp_path: Path):
    path = tmp_path / "project.yaml"
    path.write_text(BLOCK, encoding="utf-8")
    assert albums.write_album_url(path, URL)
    assert _album(path.read_text(encoding="utf-8"))["url"] == URL


def test_write_album_url_leaves_an_unknown_layout_alone(tmp_path: Path):
    path = tmp_path / "project.yaml"
    path.write_text("project: {id: p1}\n", encoding="utf-8")
    assert not albums.write_album_url(path, URL)
    assert path.read_text(encoding="utf-8") == "project: {id: p1}\n"
