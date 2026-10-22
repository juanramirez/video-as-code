// kaleidoscope - the picture mirrored into segments around the centre, turning
// Part of video-as-code (MIT)
uniform float segments;  // = 6.0
uniform float turn;      // = 0.1

vec4 effect(vec2 uv) {
  vec2 c = (uv - 0.5) * vec2(ratio, 1.0);
  float a = atan(c.y, c.x) + time * turn * 6.2832;
  float r = length(c);
  float seg = 6.2832 / max(segments, 1.0);
  a = mod(a, seg);
  a = abs(a - seg * 0.5);
  vec2 p = vec2(cos(a), sin(a)) * r / vec2(ratio, 1.0) + 0.5;
  return getColor(clamp(p, 0.0, 1.0));
}
