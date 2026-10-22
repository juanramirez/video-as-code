// glitch - bands of the picture jump sideways and split their colours, now and then
// Part of video-as-code (MIT)
uniform float intensity;  // = 0.5
uniform float rate;       // = 6.0

float hash(float n) { return fract(sin(n) * 43758.5453); }

vec4 effect(vec2 uv) {
  float tick = floor(time * rate);
  float burst = step(1.0 - intensity * 0.8, hash(tick * 1.7));
  float band = floor(uv.y * mix(6.0, 24.0, hash(tick + 3.1)));
  float shift = (hash(band + tick * 13.0) - 0.5) * 0.15 * intensity * burst;
  vec2 p = vec2(fract(uv.x + shift), uv.y);
  float split = 0.01 * intensity * burst;
  return vec4(getColor(p + vec2(split, 0.0)).r, getColor(p).g, getColor(p - vec2(split, 0.0)).b, 1.0);
}
