// vhs - a worn videotape: colour bleeding sideways, a rolling bright band, noise, jitter
// Part of video-as-code (MIT)
uniform float strength;  // = 0.6

float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

vec4 effect(vec2 uv) {
  float jitter = (hash(vec2(floor(time * 25.0), floor(uv.y * 120.0))) - 0.5) * 0.004 * strength;
  vec2 p = vec2(uv.x + jitter, uv.y);
  float bleed = 0.006 * strength;
  vec3 col = vec3(getColor(p + vec2(bleed, 0.0)).r, getColor(p).g, getColor(p - vec2(bleed, 0.0)).b);
  float band = smoothstep(0.0, 0.04, abs(fract(uv.y - time * 0.15) - 0.5) - 0.42);
  col += (1.0 - band) * 0.08 * strength;
  col += (hash(uv * resolution + time) - 0.5) * 0.12 * strength;
  return vec4(clamp(col, 0.0, 1.0), 1.0);
}
