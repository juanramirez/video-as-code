"""Run by openshot_effects.py with the Python libopenshot's bindings are
for (python3-openshot: the system's), never imported by video-as-code
itself - which may run on another Python. Standalone: only the standard
library and `openshot`.

    python3 -I openshot_render.py --describe
        -> JSON: {effect: {property: "number" | "color" | "bool" | "text"}}
    python3 -I openshot_render.py INPUT OUTPUT EFFECTS_JSON
        EFFECTS_JSON: [{"effect": "Wave", "amplitude": 0.6,
                        "wavelength": {"0": 0.06, "2": 0.2}, ...}, ...]

    python3 -I openshot_render.py --project PLAN_JSON OUTPUT.osp
        PLAN_JSON: what osp.py's `plan` makes - written out as an
        OpenShot project, each file's, clip's and effect's JSON
        libopenshot's own

A number is a keyframe held all along; a map of seconds to numbers, one
that changes (linearly) between them; a colour, [r, g, b] (0-255) or
"#rrggbb". A still in gives a still out (.png), a video a video (.mp4,
H.264, its sound - through the audio effects - as AAC).
"""
import json
import sys

import openshot


def _keyframe(value, fps):
    if isinstance(value, dict):
        points = [{"co": {"X": round(float(t) * fps) + 1, "Y": float(v)}, "interpolation": 1}
                  for t, v in sorted(value.items(), key=lambda kv: float(kv[0]))]
    else:
        points = [{"co": {"X": 1, "Y": float(value)}, "interpolation": 1}]
    return {"Points": points}


def _color(value, fps):
    if isinstance(value, str):
        value = [int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    r, g, b = value[:3]
    return {"red": _keyframe(r, fps), "green": _keyframe(g, fps), "blue": _keyframe(b, fps),
            "alpha": _keyframe(value[3] if len(value) > 3 else 255, fps)}


def _kind(value):
    if isinstance(value, dict) and "Points" in value:
        return "number"
    if isinstance(value, dict) and "red" in value:
        return "color"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    return None


# an effect's own bookkeeping, not something to set
_INTERNAL = {"id", "type", "layer", "position", "start", "end", "duration", "order", "parent_effect_id",
             "apply_before_clip", "class_name", "name", "description", "has_video", "has_audio", "has_tracked_object"}


def describe():
    info = openshot.EffectInfo()
    out = {}
    for entry in json.loads(info.Json()):
        effect = info.CreateEffect(entry["class_name"])
        props = {}
        for key, value in json.loads(effect.Json()).items():
            kind = _kind(value)
            if kind and key not in _INTERNAL:
                props[key] = kind
        out[entry["class_name"]] = {"props": props, "description": entry.get("description", ""),
                                    "audio": bool(entry.get("has_audio")) and not entry.get("has_video")}
    print(json.dumps(out))


def _effect(spec, fps):
    """One of derive:'s `openshot:` effects as libopenshot's JSON."""
    effect = openshot.EffectInfo().CreateEffect(spec["effect"])
    data = json.loads(effect.Json())
    for key, value in spec.items():
        if key == "effect":
            continue
        kind = _kind(data.get(key))
        if kind == "number":
            data[key] = _keyframe(value, fps)
        elif kind == "color":
            data[key] = _color(value, fps)
        else:
            data[key] = value
    return data


def _media_type(reader):
    if reader.get("has_video") and (reader.get("has_single_image") or reader.get("type") == "QtImageReader"):
        return "image"
    if reader.get("has_video"):
        return "video"
    return "audio" if reader.get("has_audio") else "video"


def _profile(width, height, fps):
    """The description of OpenShot's profile for this size and rate - how
    a project names its own."""
    import importlib.util
    import os
    spec = importlib.util.find_spec("openshot_qt")
    if spec and spec.submodule_search_locations:
        folder = os.path.join(list(spec.submodule_search_locations)[0], "profiles")
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            path = os.path.join(folder, name)
            if not os.path.isfile(path):  # (its `legacy` folder: libopenshot aborts on one)
                continue
            try:
                profile = openshot.Profile(path)  # (kept: its `info` lives inside it)
            except RuntimeError:
                continue
            info = profile.info
            if (info.width, info.height, info.fps.num, info.fps.den) == (width, height, fps["num"], fps["den"]):
                return info.description
    return "%dx%d %g fps" % (width, height, fps["num"] / fps["den"])


def project(plan_path, dst):
    plan = json.loads(open(plan_path, encoding="utf-8").read())
    fps = plan["fps"]
    rate = fps["num"] / fps["den"]
    files, file_id = [], {}
    for path in plan["files"]:
        clip = openshot.Clip(path)
        clip.Open()
        reader = json.loads(clip.Reader().Json())
        clip.Close()
        reader["id"] = "F" + str(len(files)).zfill(9)
        reader["media_type"] = _media_type(reader)
        file_id[path] = reader["id"]
        files.append(reader)
    clips = []
    for spec in plan["clips"]:
        clip = openshot.Clip(spec["path"])
        clip.Open()
        data = json.loads(clip.Json())
        clip.Close()
        data.update({key: spec[key] for key in ("id", "title", "layer", "position", "start", "end")})
        data.update(spec["props"])
        data["file_id"] = file_id[spec["path"]]
        data["effects"] = []
        for i, effect in enumerate(spec["effects"]):
            effect_data = _effect(effect, rate)
            effect_data["id"] = spec["id"][:8] + "E" + str(i)
            data["effects"].append(effect_data)
        clips.append(data)
    effects = []
    for spec in plan["transitions"]:
        reader = json.loads(openshot.QtImageReader(spec["picture"]).Json())
        effects.append({"id": spec["id"], "layer": spec["layer"], "title": "Transition", "type": "Mask",
                        "position": spec["position"], "start": 0, "end": spec["end"],
                        "brightness": spec["brightness"], "contrast": spec["contrast"],
                        "reader": reader, "replace_image": False})
    out = {key: plan[key] for key in ("id", "fps", "display_ratio", "pixel_ratio", "width", "height",
                                      "sample_rate", "channels", "channel_layout", "duration", "layers", "markers")}
    out.update({
        "settings": {}, "clips": clips, "effects": effects, "files": files,
        "scale": 15.0, "tick_pixels": 100, "playhead_position": 0,
        "profile": _profile(plan["width"], plan["height"], fps), "export_settings": None,
        "progress": [], "history": {"undo": [], "redo": []},
        "version": {"openshot-qt": _openshot_qt_version(), "libopenshot": openshot.OPENSHOT_VERSION_FULL},
    })
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)


def _openshot_qt_version():
    """OpenShot's own version, which a project records (so it knows which
    upgrades its data needs): from openshot-qt if it's there."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("openshot_qt")
        folder = list(spec.submodule_search_locations)[0]
        sys.path.insert(0, folder)
        from classes import info
        return info.VERSION
    except Exception:
        return "3.0.0"


def render(src, dst, effects):
    clip = openshot.Clip(src)
    clip.Open()
    reader = clip.Reader()
    info = reader.info
    fps = info.fps.ToFloat() if info.fps.den else 25.0
    factory = openshot.EffectInfo()
    for spec in effects:
        effect = factory.CreateEffect(spec["effect"])
        effect.SetJson(json.dumps(_effect(spec, fps)))
        clip.AddEffect(effect)
    if dst.endswith(".png"):  # a still: its one frame
        clip.GetFrame(1).Save(dst, 1.0)
        clip.Close()
        return
    writer = openshot.FFmpegWriter(dst)
    if info.has_video:
        writer.SetVideoOptions(True, "libx264", info.fps, info.width, info.height,
                               openshot.Fraction(1, 1), False, False, 8000000)
    if info.has_audio:
        writer.SetAudioOptions(True, "aac", info.sample_rate, info.channels, info.channel_layout, 192000)
    writer.Open()
    for n in range(1, info.video_length + 1):
        writer.WriteFrame(clip.GetFrame(n))
    writer.Close()
    clip.Close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--describe"]:
        describe()
    elif sys.argv[1] == "--project":
        project(sys.argv[2], sys.argv[3])
    else:
        render(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
