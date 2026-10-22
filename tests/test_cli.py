"""Tests for `cli.py`'s `render` subcommand: builds a .kdenlive from the
YAML and renders it straight to a video via a real `melt`, instead of
leaving that second step to the user."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_as_code import cli, i18n, probe


def _write_simple_project(tmp_path: Path, assets_dir: Path) -> Path:
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
timeline:
  tracks:
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1}}
""",
        encoding="utf-8",
    )
    return yaml_path


def test_render_missing_melt(tmp_path: Path, assets_dir: Path, monkeypatch, capsys):
    monkeypatch.setattr(probe, "find_melt", lambda: None)
    yaml_path = _write_simple_project(tmp_path, assets_dir)

    rc = cli.main(["render", str(yaml_path)])

    assert rc == 1
    assert "melt" in capsys.readouterr().err.lower()


def test_lang_flag_overrides_locale(tmp_path: Path, assets_dir: Path, capsys):
    yaml_path = _write_simple_project(tmp_path, assets_dir)
    original_locale = i18n.LOCALE
    try:
        rc = cli.main(["--lang", "es", "validate", str(yaml_path)])
        assert rc == 0
        assert "pista" in capsys.readouterr().out
    finally:
        i18n.set_locale(original_locale)


def test_build_write_error_reported_cleanly_not_as_a_traceback(tmp_path: Path, assets_dir: Path, capsys):
    """A PermissionError/OSError writing the output (here: the output path
    is a directory, not a file) should go through the same clean,
    i18n-routed error path every other CLI failure mode uses - not an
    unhandled Python traceback."""
    yaml_path = _write_simple_project(tmp_path, assets_dir)
    output_dir = tmp_path / "out_is_a_dir"
    output_dir.mkdir()

    rc = cli.main(["build", str(yaml_path), "-o", str(output_dir)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(output_dir) in err


def test_render_end_to_end(tmp_path: Path, assets_dir: Path, melt, monkeypatch):
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)

    yaml_path = _write_simple_project(tmp_path, assets_dir)
    output = tmp_path / "out.mp4"

    rc = cli.main(["render", str(yaml_path), "-o", str(output)])

    assert rc == 0
    assert output.exists() and output.stat().st_size > 0


def test_render_creates_the_output_folder(tmp_path: Path, assets_dir: Path, melt, monkeypatch):
    """`-o output/x.mp4` in a fresh clone, where output/ doesn't exist yet."""
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)

    yaml_path = _write_simple_project(tmp_path, assets_dir)
    output = tmp_path / "output" / "new" / "out.mp4"

    assert cli.main(["render", str(yaml_path), "-o", str(output)]) == 0
    assert output.exists() and output.stat().st_size > 0
    output.unlink()
    assert cli.main(["render", str(yaml_path), "-o", str(output), "--split"]) == 0
    assert output.exists() and output.stat().st_size > 0


def test_render_keep_kdenlive(tmp_path: Path, assets_dir: Path, melt, monkeypatch):
    """`-k`/`--keep-kdenlive` (new this cycle) should leave the
    intermediate .kdenlive next to the YAML instead of discarding it in a
    temp dir - it's the same file `build` would produce, not a stub."""
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)

    yaml_path = _write_simple_project(tmp_path, assets_dir)
    output = tmp_path / "out.mp4"
    expected_kdenlive = yaml_path.with_suffix(".kdenlive")

    rc = cli.main(["render", str(yaml_path), "-o", str(output), "-k"])

    assert rc == 0
    assert output.exists() and output.stat().st_size > 0
    assert expected_kdenlive.exists() and expected_kdenlive.stat().st_size > 0


def test_render_runs_melt_under_c_locale(tmp_path: Path, assets_dir: Path, monkeypatch):
    """`render` must hand melt `LC_ALL=C` whatever the caller's own locale
    is - see probe.melt_env for what goes wrong otherwise."""
    monkeypatch.setattr(probe, "find_melt", lambda: "melt")
    monkeypatch.setenv("LC_NUMERIC", "es_ES.UTF-8")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    yaml_path = _write_simple_project(tmp_path, assets_dir)

    rc = cli.main(["render", str(yaml_path), "-o", str(tmp_path / "out.mp4")])

    assert rc == 0
    assert seen["env"]["LC_ALL"] == "C"


def _es_locale_available() -> bool:
    try:
        out = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.lower()
    except OSError:
        return False
    return "es_es.utf8" in out or "es_es.utf-8" in out


def _max_volume_db(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    line = next(l for l in result.stderr.splitlines() if "max_volume" in l)
    return float(line.split("max_volume:")[1].split("dB")[0])


@pytest.mark.skipif(not _es_locale_available(), reason="needs the es_ES.UTF-8 locale installed")
def test_render_honors_decimal_gain_under_comma_decimal_locale(tmp_path: Path, assets_dir: Path, melt, monkeypatch):
    """End-to-end regression for the locale bug: rendered from a shell
    whose LC_NUMERIC uses a decimal comma, `gain: 1.5` must still come out
    ~3.5 dB louder than `gain: 1.0` - without the fix, MLT read "1.5" as 1
    and both renders were identical."""
    exe, extra_env = melt
    monkeypatch.setattr(probe, "find_melt", lambda: exe)
    for k, v in extra_env.items():
        if k != "LC_ALL":  # the point is that `render` itself must set it
            monkeypatch.setenv(k, v)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LC_NUMERIC", "es_ES.UTF-8")

    levels = {}
    for gain in ("1.0", "1.5"):
        yaml_path = tmp_path / f"p_{gain}.yaml"
        yaml_path.write_text(
            f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 2, gain: {gain}}}
""",
            encoding="utf-8",
        )
        output = tmp_path / f"out_{gain}.mp4"
        assert cli.main(["render", str(yaml_path), "-o", str(output)]) == 0
        levels[gain] = _max_volume_db(output)

    assert levels["1.5"] - levels["1.0"] > 2.5, levels


def test_find_melt_honors_video_as_code_melt(tmp_path: Path, monkeypatch):
    from video_as_code import probe

    melt = tmp_path / "melt"
    melt.write_text("#!/bin/sh\n")
    monkeypatch.setenv("VIDEO_AS_CODE_MELT", str(melt))
    assert probe.find_melt() == str(melt)
    monkeypatch.setenv("VIDEO_AS_CODE_MELT", str(tmp_path / "missing"))
    assert probe.find_melt() is None


def test_transitions_lists_own_and_openshot(openshot_transitions, capsys):
    assert cli.main(["transitions"]) == 0
    names = capsys.readouterr().out.split()
    assert names[0] == "linear_x"
    assert {"openshot/ramp_left", "openshot/ramp_top"} <= set(names)
