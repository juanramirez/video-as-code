"""Tests for `cli.py`'s `render` subcommand: builds a .kdenlive from the
YAML and renders it straight to a video via a real `melt`, instead of
leaving that second step to the user."""
from __future__ import annotations

from pathlib import Path

from kdenlive_generator import cli, probe


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
