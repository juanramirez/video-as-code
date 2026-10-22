// crt - an old CRT screen: a curved picture, scanlines, darker corners
// Part of video-as-code (MIT)
uniform float curvature;  // = 0.15
uniform float scanlines;  // = 0.35
uniform float vignette;   // = 0.4

vec4 effect(vec2 uv) {
  vec2 c = uv * 2.0 - 1.0;
  c *= 1.0 + curvature * dot(c.yx, c.yx) * 0.5;
  vec2 p = c * 0.5 + 0.5;
  if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0) return vec4(0.0, 0.0, 0.0, 1.0);
  vec3 col = getColor(p).rgb;
  float line = 0.5 + 0.5 * sin(p.y * resolution.y * 3.14159);
  col *= 1.0 - scanlines * (1.0 - line);
  col *= 1.0 - vignette * smoothstep(0.4, 1.4, length(c));
  return vec4(col, 1.0);
}
