"""Validation against a real `melt` (MLT engine) binary: if the binary isn't
available on this system, these tests are skipped (see the `melt` fixture in
conftest.py) instead of failing — they're an extra confidence check, not a
requirement for generating projects."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from kdenlive_generator import xmlgen
from kdenlive_generator.schema import load_and_resolve


def _build(tmp_path: Path, assets_dir: Path) -> Path:
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
bin:
  - id: photo
    path: {assets_dir / "photo.png"}
  - id: clip
    path: {assets_dir / "clip.mp4"}
  - id: tone
    path: {assets_dir / "tone.mp3"}
timeline:
  tracks:
    - type: audio
      clips:
        - {{clip: tone, start: 0, out: 3, fade_out: 0.5}}
    - type: video
      clips:
        - {{clip: photo, start: 0, duration: 1.5, fade_in: 0.5}}
        - {{clip: clip, start: 1.5, duration: 2}}
""",
        encoding="utf-8",
    )
    resolved = load_and_resolve(yaml_path)
    out_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, out_path)
    return out_path


def test_melt_can_load_generated_project(tmp_path: Path, assets_dir: Path, melt):
    exe, extra_env = melt
    out_path = _build(tmp_path, assets_dir)
    xml_out = tmp_path / "roundtrip.xml"

    env = {**os.environ, **extra_env}
    result = subprocess.run(
        [exe, str(out_path), "-consumer", f"xml:{xml_out}", "all_frames=0"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    stderr_lines = [
        line
        for line in result.stderr.splitlines()
        # the test runner invokes the snap's melt-7 "raw", outside its
        # confinement, so it's missing the snap's own Qt6 and can't load the
        # "qtblend" service (see README); inside real kdenlive that library
        # is available. This isn't a failure of the generated XML.
        if "qtblend" not in line and "libmltqt6" not in line
    ]
    fatal = [l for l in stderr_lines if "Failed to load" in l and "project.kdenlive" in l]

    assert not fatal, "melt failed to load the generated project:\n" + "\n".join(stderr_lines)
    assert xml_out.exists() and xml_out.stat().st_size > 0
