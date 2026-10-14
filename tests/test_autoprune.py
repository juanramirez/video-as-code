"""autoprune.py: what a render deletes beforehand, and what it never does."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

import pytest

from video_as_code import autoprune, sources_lock
from video_as_code.schema import load_and_resolve, load_yaml

DAY = 24 * 3600


def _project(tmp_path: Path, assets_dir: Path, extra: str = "") -> Path:
    """A derive: whose album input (VID_1.mp4) is on disk and locked."""
    sources = tmp_path / "assets"
    sources.mkdir()
    shutil.copy(assets_dir / "av.mp4", sources / "VID_1.mp4")
    body = (sources / "VID_1.mp4").read_bytes()
    sources_lock.write(
        tmp_path,
        entries={"VID_1.mp4": {"media_id": "m1", "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}},
        links={},
    )
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240, sources: {{album: 'https://photos.app.goo.gl/x'{extra}}}}}
bin:
  - {{id: cut, derive: {{source: VID_1.mp4, trim: [0, 1]}}}}
timeline:
  tracks: []
""",
        encoding="utf-8",
    )
    return path


def _prune(project: Path, now: float, needed=frozenset()):
    doc = load_yaml(project)
    return autoprune.prune(doc, set(needed), doc.project.prune_after_days, now=now)


def test_a_derive_only_input_goes_after_the_days_without_use(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, assets_dir)
    # generates the output (its sidecar records the input's sha256)
    needed = autoprune.used_paths(load_and_resolve(project))  # the output, not its input
    original = tmp_path / "assets" / "VID_1.mp4"
    assert _prune(project, now=time.time() + 6 * DAY, needed=needed) == ([], 0)  # 6 days: kept
    deleted, freed = _prune(project, now=time.time() + 8 * DAY, needed=needed)
    assert deleted == [original] and freed > 0 and not original.exists()
    load_and_resolve(project)  # the output still stands on its own


def test_a_file_seen_for_the_first_time_is_only_recorded(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, assets_dir)
    stale = tmp_path / "assets" / ".derived" / "old-0123456789.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    autoprune.record_use(tmp_path, [])  # nothing recorded about it yet
    # no record and no sidecar: never "old"
    assert stale not in _prune(project, now=time.time() + 30 * DAY)[0]


def test_stale_generated_files_go_with_their_sidecars(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, assets_dir)
    stale = tmp_path / "assets" / ".derived" / "old-0123456789.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    sidecar = stale.with_name(stale.name + ".input.json")
    sidecar.write_text("{}")
    long_ago = time.time() - 10 * DAY
    os.utime(sidecar, (long_ago, long_ago))  # generated back then too
    autoprune.record_use(tmp_path, [stale], now=long_ago)
    deleted, _ = _prune(project, now=time.time())
    assert stale in deleted and not stale.exists() and not sidecar.exists()


def test_what_the_render_needs_and_what_only_this_machine_has_stay(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, assets_dir)
    resolved = load_and_resolve(project)
    output = resolved.bin_clips[0].path
    local_only = tmp_path / "assets" / "my_own_cut.mp4"  # not in the lock: can't be brought back
    local_only.write_bytes(b"y")
    long_ago = time.time() - 100 * DAY
    autoprune.record_use(tmp_path, [output, local_only], now=long_ago)
    deleted, _ = _prune(project, now=time.time(), needed={output})
    assert output.exists() and local_only.exists()
    assert output not in deleted and local_only not in deleted


def test_zero_days_turns_it_off(tmp_path: Path, assets_dir: Path):
    project = _project(tmp_path, assets_dir, extra=", prune_after_days: 0")
    load_and_resolve(project)
    assert _prune(project, now=time.time() + 365 * DAY) == ([], 0)
    assert (tmp_path / "assets" / "VID_1.mp4").exists()


@pytest.mark.parametrize("value", ["-1", "'soon'", "true"])
def test_invalid_prune_after_days(tmp_path: Path, assets_dir: Path, value: str):
    from video_as_code.errors import KdenliveAsCodeError

    project = _project(tmp_path, assets_dir, extra=f", prune_after_days: {value}")
    with pytest.raises(KdenliveAsCodeError, match="prune_after_days"):
        load_and_resolve(project)
