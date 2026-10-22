"""Run by openshot_effects.py with the Python libopenshot's bindings are
for (python3-openshot: the system's), never imported by video-as-code
itself - which may run on another Python. Standalone: only the standard
library and `openshot`.

    python3 -I openshot_render.py --describe
        -> JSON: {effect: {property: "number" | "color" | "bool" | "text"}}
    python3 -I openshot_render.py INPUT OUTPUT EFFECTS_JSON
        EFFECTS_JSON: [{"effect": "Wave", "amplitude": 0.6,
                        "wavelength": {"0": 0.06, "2": 0.2}, ...}, ...]

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


def render(src, dst, effects):
    clip = openshot.Clip(src)
    clip.Open()
    reader = clip.Reader()
    info = reader.info
    fps = info.fps.ToFloat() if info.fps.den else 25.0
    factory = openshot.EffectInfo()
    for spec in effects:
        effect = factory.CreateEffect(spec["effect"])
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
        effect.SetJson(json.dumps(data))
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
    else:
        render(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
