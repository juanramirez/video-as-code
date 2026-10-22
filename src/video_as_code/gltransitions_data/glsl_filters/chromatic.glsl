// chromatic - chromatic aberration: red and blue pulled apart toward the edges
// Part of video-as-code (MIT)
uniform float amount;  // = 0.012

vec4 effect(vec2 uv) {
  vec2 d = (uv - 0.5) * amount;
  float r = getColor(uv + d).r;
  float g = getColor(uv).g;
  float b = getColor(uv - d).b;
  return vec4(r, g, b, 1.0);
}
