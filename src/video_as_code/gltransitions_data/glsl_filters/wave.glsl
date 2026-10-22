// wave - the picture ripples, as seen through moving water
// Part of video-as-code (MIT)
uniform float amplitude;  // = 0.012
uniform float frequency;  // = 14.0
uniform float speed;      // = 2.0

vec4 effect(vec2 uv) {
  vec2 p = uv;
  p.x += amplitude * sin(uv.y * frequency + time * speed * 6.2832);
  p.y += amplitude * 0.6 * cos(uv.x * frequency * 0.8 + time * speed * 5.0);
  return getColor(clamp(p, 0.0, 1.0));
}
