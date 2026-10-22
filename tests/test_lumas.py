"""lumas.py's OpenShot transitions (`luma: openshot/<name>`): found in the
user's openshot-qt install, converted to PGM once, recognized on import -
against a stand-in folder (conftest.py's openshot_transitions)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_as_code import lumas
from video_as_code.errors import KdenliveAsCodeError


def test_transitions_found_across_common_and_extra(openshot_transitions: Path):
    found = lumas.openshot_transitions()
    assert set(found) == {"ramp_left", "ramp_top"}
    assert lumas.luma_problem("t", "openshot/ramp_left", "validation.transition_luma_invalid") is None


def test_unknown_name_suggests_the_closest(openshot_transitions: Path):
    problem = lumas.luma_problem("t", "openshot/ramp_lef", "validation.transition_luma_invalid")
    assert "openshot/ramp_left" in problem


def test_a_name_cant_reach_outside_the_folder(openshot_transitions: Path):
    assert lumas.luma_problem("t", "openshot/../extra/ramp_left", "validation.transition_luma_invalid")


def test_missing_install_says_how_to_get_it(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(lumas.OPENSHOT_DIR_ENV, str(tmp_path / "nowhere"))
    problem = lumas.luma_problem("t", "openshot/twirl_1", "validation.transition_luma_invalid")
    assert "openshot-qt" in problem and lumas.OPENSHOT_DIR_ENV in problem
    with pytest.raises(KdenliveAsCodeError):
        lumas.luma_path("openshot/twirl_1")


def test_own_names_unchanged(openshot_transitions: Path):
    assert lumas.luma_path("radial") == lumas.LUMAS_DIR / "radial.pgm"
    assert lumas.luma_problem("t", "radial", "validation.transition_luma_invalid") is None
    assert "linear_x" in lumas.luma_problem("t", "nope", "validation.transition_luma_invalid")


def test_converted_to_pgm_once_and_again_when_the_map_changes(openshot_transitions: Path, tmp_path: Path):
    path = lumas.luma_path("openshot/ramp_left")
    assert path.parent == tmp_path / "cache" / "video-as-code" / lumas.OPENSHOT_CACHE
    assert path.read_bytes().startswith(b"P5")  # binary PGM, what MLT reads directly
    stamp = path.stat().st_mtime_ns
    assert lumas.luma_path("openshot/ramp_left").stat().st_mtime_ns == stamp  # reused

    source = openshot_transitions / "extra" / "ramp_left.jpg"
    later = path.stat().st_mtime + 10
    os.utime(source, (later, later))
    assert lumas.luma_path("openshot/ramp_left").stat().st_mtime_ns != stamp


def test_name_from_resource(openshot_transitions: Path):
    assert lumas.name_from_resource(str(lumas.luma_path("openshot/ramp_top"))) == "openshot/ramp_top"
    assert lumas.name_from_resource(str(lumas.luma_path("spiral"))) == "spiral"
    assert lumas.name_from_resource("/somewhere/else/luma01.pgm") is None


@pytest.mark.parametrize("value", [-0.1, 1.5, "soft", True])
def test_softness_out_of_range(value):
    errors: list[str] = []
    assert lumas.wipe_softness(value, "t", "softness", errors) is None
    assert errors


def test_unknown_name_with_nothing_close(openshot_transitions: Path):
    problem = lumas.luma_problem("t", "openshot/zzzzzz", "validation.transition_luma_invalid")
    assert "openshot/zzzzzz" in problem and "[]" not in problem
