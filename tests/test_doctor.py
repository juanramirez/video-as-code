"""doctor.py: what's there and what isn't, and how to get it - the exit
code 1 only for what nothing renders without."""
from __future__ import annotations

import pytest

from video_as_code import cli, doctor, gltransitions, openshot_effects, probe, splitrender, titles, upscale


ALL = {
    "producers": frozenset({"kdenlivetitle", "timewarp", "glaxnimate"}),
    "filters": frozenset({"frei0r.alphaspot", "avfilter.lut3d", "deshake", "frei0r.glfilter"}),
    "transitions": frozenset({"qtblend", "frei0r.gltransition"}),
}


@pytest.fixture
def machine(monkeypatch):
    """Everything there, until a test takes something away."""
    monkeypatch.setattr(probe, "find_melt", lambda: "/usr/bin/melt")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor, "_version", lambda cmd, word: "1.0")
    monkeypatch.setattr(doctor, "melt_services", lambda melt: dict(ALL))
    monkeypatch.setattr(probe, "melt_version", lambda melt: (7, 36, 1))
    monkeypatch.setattr(titles, "melt_weight_scale", lambda: 6)
    monkeypatch.setattr(gltransitions, "installed", lambda plugin=gltransitions.PLUGIN: True)
    monkeypatch.setattr(openshot_effects, "version", lambda: "0.5.0")
    monkeypatch.setattr(upscale, "find_binary", lambda: "/x/realesrgan-ncnn-vulkan")
    monkeypatch.setattr(splitrender, "_scope_available", lambda: True)
    monkeypatch.setenv("VIDEO_AS_CODE_PLAYER", "vlc {file}")
    return monkeypatch


def _run() -> tuple[int, str]:
    said = []
    code = doctor.run(said.append)
    return code, "\n".join(said)


def test_all_there(machine):
    code, text = _run()
    assert code == 0 and "–" not in text and "✗" not in text
    assert text.endswith("Everything is there.")


def test_no_melt_is_needed(machine):
    machine.setattr(probe, "find_melt", lambda: None)
    code, text = _run()
    assert code == 1
    assert "✗ melt" in text and "sudo apt install melt" in text
    assert "melt's modules" not in text  # (no melt to ask)


def test_a_module_missing_says_what_needs_it(machine):
    machine.setattr(doctor, "melt_services", lambda melt: {**ALL, "producers": frozenset({"kdenlivetitle", "timewarp"})})
    code, text = _run()
    assert code == 0  # (only Lottie animations won't render)
    assert "– Lottie animations (glaxnimate): this melt has no glaxnimate" in text
    assert "1 optional thing(s) missing" in text


def test_a_plugin_built_that_melt_doesnt_load(machine):
    machine.setattr(doctor, "melt_services", lambda melt: {**ALL, "transitions": frozenset({"qtblend"})})
    _, text = _run()
    assert "built, but this melt doesn't load it (frei0r.gltransition)" in text
    assert "video-as-code install-gl-transitions" in text


def test_no_frei0r_at_all(machine):
    machine.setattr(doctor, "melt_services", lambda melt: {k: frozenset(n for n in v if not n.startswith("frei0r."))
                                                           for k, v in ALL.items()})
    _, text = _run()
    assert "sudo apt install frei0r-plugins" in text


def test_versions_too_old(machine):
    # (Debian 12's: MLT 7.12, libopenshot 0.2.7; Debian 13's libopenshot 0.3.3)
    machine.setattr(probe, "melt_version", lambda melt: (7, 12, 0))
    machine.setattr(openshot_effects, "version", lambda: "0.3.3")
    code, text = _run()
    assert code == 0
    assert "– speed_ramp: (timeremap): this melt is 7.12.0; speed_ramp: needs MLT 7.22 or later" in text
    assert "0.3.3; derive: {openshot: ...} needs 0.5.0 or later" in text


def test_titles_drawn_with_qt5_said(machine):
    machine.setattr(titles, "melt_weight_scale", lambda: 5)
    code, text = _run()
    assert code == 0 and "✓ title clips (kdenlivetitle): drawn with Qt 5" in text


def test_the_real_melt_is_asked(melt):
    services = doctor.melt_services(melt[0])
    assert "color" in services["producers"] and "luma" in services["transitions"]


def test_the_command(machine, capsys):
    assert cli.main(["doctor"]) == 0
    assert "Everything is there." in capsys.readouterr().out
