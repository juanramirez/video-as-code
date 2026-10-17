"""mltdata.py: the per-render copy of MLT's data folder that decides what
the `dust` filter draws (none of its pictures -> dots; chosen ones), and
how a render finds MLT's data folder in the first place."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_as_code import mltdata, probe


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A stand-in for <prefix>/share/mlt-7: oldfilm/ with MLT's five dust
    pictures and something else, plus another module's folder."""
    d = tmp_path / "prefix" / "share" / "mlt-7"
    (d / "oldfilm").mkdir(parents=True)
    for i in range(1, 6):
        (d / "oldfilm" / f"dust{i}.svg").write_text(f"<svg>{i}</svg>")
    (d / "oldfilm" / "grain.svg").write_text("<svg>grain</svg>")
    (d / "frei0r").mkdir()
    (d / "frei0r" / "x.yml").write_text("x")
    return d


def _dust(folder: Path) -> list[str]:
    return sorted(p.name for p in (folder / "oldfilm").glob("dust*.svg"))


def test_dots_folder_has_no_dust_pictures_and_keeps_the_rest(data_dir: Path, tmp_path: Path):
    out = mltdata.dots_data_dir(data_dir, tmp_path / "cache")
    assert _dust(out) == []
    assert (out / "oldfilm" / "grain.svg").read_text() == "<svg>grain</svg>"
    assert (out / "frei0r").is_symlink() and (out / "frei0r" / "x.yml").read_text() == "x"
    assert out.name.startswith("mlt-data-dots-")


def test_pictures_folder_has_exactly_the_chosen_ones_repeats_included(data_dir: Path, tmp_path: Path):
    speck = tmp_path / "speck.svg"
    speck.write_text("<svg>speck</svg>")
    out = mltdata.pictures_data_dir(data_dir, tmp_path / "cache", ("dust3", str(speck), str(speck)))
    # a repeated picture is a second name for it: drawn twice as often
    assert _dust(out) == ["dust01.svg", "dust02.svg", "dust03.svg"]
    assert [(out / "oldfilm" / n).read_text() for n in _dust(out)] == ["<svg>3</svg>", "<svg>speck</svg>", "<svg>speck</svg>"]
    assert out.name.startswith("mlt-data-dust-")


def test_the_folder_is_reused_and_changes_with_the_pictures_contents(data_dir: Path, tmp_path: Path):
    speck = tmp_path / "speck.svg"
    speck.write_text("<svg>a</svg>")
    first = mltdata.pictures_data_dir(data_dir, tmp_path / "cache", (str(speck),))
    assert mltdata.pictures_data_dir(data_dir, tmp_path / "cache", (str(speck),)) == first
    speck.write_text("<svg>b</svg>")  # same path, new drawing: a new folder, not a stale one
    assert mltdata.pictures_data_dir(data_dir, tmp_path / "cache", (str(speck),)) != first


def test_find_data_dir_next_to_melt_or_from_mlt_data(data_dir: Path, tmp_path: Path, monkeypatch):
    melt = data_dir.parent.parent / "bin" / "melt"
    melt.parent.mkdir()
    melt.write_text("")
    monkeypatch.delenv("MLT_DATA", raising=False)
    assert mltdata.find_data_dir(str(melt)) == data_dir
    other = tmp_path / "elsewhere"
    (other / "oldfilm").mkdir(parents=True)
    monkeypatch.setenv("MLT_DATA", str(other))
    assert mltdata.find_data_dir(str(melt)) == other  # MLT_DATA wins
    monkeypatch.setenv("MLT_DATA", str(tmp_path / "nothing"))
    assert mltdata.find_data_dir(str(tmp_path / "no" / "bin" / "melt")) is None


def test_default_cache_root_follows_xdg(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert mltdata.default_cache_root() == tmp_path / "video-as-code"


def test_melt_env_sets_mlt_data_only_when_given(tmp_path: Path):
    assert "MLT_DATA" not in probe.melt_env({"PATH": os.environ.get("PATH", "")})
    assert probe.melt_env({}, mlt_data=tmp_path)["MLT_DATA"] == str(tmp_path)
