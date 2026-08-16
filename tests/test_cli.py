"""Tests for `cli.py`'s `render` subcommand: builds a .kdenlive from the
YAML and renders it straight to a video via a real `melt`, instead of
leaving that second step to the user."""
from __future__ import annotations

from pathlib import Path

from kdenlive_as_code import cli, i18n, probe


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
