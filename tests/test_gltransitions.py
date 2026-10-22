"""`transition: gl` (gltransitions.py): the bundled catalog, its
parameters checked and passed whole, the plugin built and found by melt,
and real renders - which way a wipe goes, and a vec3 parameter arriving."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from video_as_code import gltransitions, probe, reverse, xmlgen
from video_as_code.errors import ValidationErrors
from video_as_code.schema import load_and_resolve


@pytest.fixture(scope="session")
def _built_plugin(tmp_path_factory) -> Path:
    """The plugin, built once into a data folder of the tests' own."""
    data = tmp_path_factory.mktemp("xdg_data")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_DATA_HOME", str(data))
        try:
            gltransitions.install()
        except gltransitions.GlTransitionsError as exc:
            pytest.skip(f"can't build the plugin here: {exc}")
    return data


@pytest.fixture
def plugin(_built_plugin: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(_built_plugin))
    return gltransitions.install_dir() / gltransitions.PLUGIN


@pytest.fixture
def no_plugin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))


def test_catalog_is_whole_and_free():
    catalog = gltransitions.catalog()
    assert len(catalog) == 125
    for name, info in catalog.items():
        assert gltransitions.shader_path(name).is_file(), name
        assert info["license"] in ("MIT", "BSD 2 Clause", "BSD 3 Clause"), name
        for param in info["params"].values():
            assert "default" in param and "type" in param


def _parse(name, params=None):
    errors: list[str] = []
    return gltransitions.parse(name, params, "t", errors), errors


def test_every_parameter_passed_with_the_defaults(plugin):
    service, errors = _parse("fadecolor", {"color": [0, 1, 0]})
    assert errors == []
    props = dict(service.properties)
    assert service.service == "frei0r.gltransition"
    assert props["shader"] == str(gltransitions.shader_path("fadecolor"))
    assert props["progress"] == "0=0;-1=1"
    # no "=" nor ";": MLT would read those as keyframes (gltransition.c)
    assert props["uniforms"] == "color:0,1,0 colorPhase:0.4"


@pytest.mark.parametrize(
    "name, params, expected",
    [
        ("cubee", None, "cube"),
        ("cube", {"perspective": 0.5}, "persp"),
        ("cube", {"persp": 2}, "float 0..1"),
        ("cube", {"persp": "much"}, "float"),
        ("fadecolor", {"color": [1, 0]}, "vec3"),
        ("GridFlip", {"size": [4.5, 4]}, "ivec2"),
        ("BowTieWithParameter", {"reverse": 1}, "bool"),
        ("cube", [0.5], "gl_params"),
    ],
)
def test_wrong_names_and_values_rejected(plugin, name, params, expected):
    service, errors = _parse(name, params)
    assert service is None and len(errors) == 1 and expected in errors[0], errors


def test_without_the_plugin_says_how_to_build_it(no_plugin):
    service, errors = _parse("cube")
    assert service is None and "install-gl-transitions" in errors[0]


def test_installed_only_from_the_current_source(plugin):
    assert gltransitions.installed()
    stamp = plugin.with_name(plugin.name + ".source-sha256")
    good = stamp.read_text()
    try:
        stamp.write_text("an older source\n")
        assert not gltransitions.installed()
    finally:
        stamp.write_text(good)


def test_melt_finds_the_plugin_first(plugin):
    env = probe.melt_env({"FREI0R_PATH": "/theirs"})
    assert env["FREI0R_PATH"] == f"{plugin.parent}:/theirs"


def _scene_project(tmp_path: Path, assets_dir: Path, keys: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(
        f"""
project: {{id: p1, title: T, fps: 25, width: 320, height: 240}}
scenes:
  - scene: s1
    transition: gl
{keys}
    transition_duration: 1.0
    clips:
      - {assets_dir / "photo.png"}
      - {assets_dir / "photo2.png"}
    duration: 4.0
""",
        encoding="utf-8",
    )
    return path


def test_scene_and_import(plugin, tmp_path: Path, assets_dir: Path):
    path = _scene_project(tmp_path, assets_dir, "    gl: cube\n    gl_params: {persp: 0.5}")
    resolved = load_and_resolve(path)
    (t,) = resolved.melt_transitions
    assert t.melt.service == "frei0r.gltransition"

    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(resolved, kdenlive_path)
    (back,) = reverse.import_kdenlive(kdenlive_path).data["transitions"]
    assert (back["type"], back["gl"], back["gl_params"]) == ("gl", "cube", {"persp": 0.5})


def test_scene_rejects_an_unknown_one(plugin, tmp_path: Path, assets_dir: Path):
    with pytest.raises(ValidationErrors) as exc:
        load_and_resolve(_scene_project(tmp_path, assets_dir, "    gl: nope"))
    assert "nope" in str(exc.value)


def _mean_rgb(path: Path, t: float, crop: str) -> tuple[float, float, float]:
    data = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", f"crop={crop}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    n = len(data) // 3
    return sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n


def _render(path: Path, tmp_path: Path) -> Path:
    melt = probe.find_melt()
    if melt is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs melt and ffmpeg")
    kdenlive_path = tmp_path / "project.kdenlive"
    xmlgen.write(load_and_resolve(path), kdenlive_path)
    out = tmp_path / "out.mp4"
    result = subprocess.run([melt, str(kdenlive_path), "-consumer", f"avformat:{out}"], env=probe.melt_env(),
                            capture_output=True, text=True)
    if "no EGL context" in result.stderr:
        pytest.skip("no EGL context with GLES 3 here")
    assert result.returncode == 0, result.stderr[-500:]
    return out


def test_wipes_go_the_shaders_way(plugin, tmp_path: Path, assets_dir: Path):
    """wipeLeft brings the second clip in from the right, wipeUp from the
    bottom (gl-transitions' uv is bottom-up, frei0r's pictures top-down:
    gltransition.c flips both ways)."""
    # red -> blue over [1.5, 2.5)s; at 1.8 a third of the way
    out = _render(_scene_project(tmp_path, assets_dir, "    gl: wipeLeft"), tmp_path)
    left, right = _mean_rgb(out, 1.8, "16:240:0:0"), _mean_rgb(out, 1.8, "16:240:304:0")
    assert left[0] > 200 and right[2] > 200, (left, right)

    up = tmp_path / "up"
    up.mkdir()
    out = _render(_scene_project(up, assets_dir, "    gl: wipeUp"), up)
    top, bottom = _mean_rgb(out, 1.8, "320:16:0:0"), _mean_rgb(out, 1.8, "320:16:0:224")
    assert top[0] > 200 and bottom[2] > 200, (top, bottom)


def test_a_vector_parameter_arrives(plugin, tmp_path: Path, assets_dir: Path):
    """fadecolor through green: halfway, the frame is green - only if the
    vec3 reaches the shader (it once didn't, see gltransition.c)."""
    out = _render(_scene_project(tmp_path, assets_dir, "    gl: fadecolor\n    gl_params: {color: [0, 1, 0]}"), tmp_path)
    r, g, b = _mean_rgb(out, 2.0, "320:240:0:0")
    assert g > 200 and r < 60 and b < 60, (r, g, b)
