// fade — GLSL ES 3.00, generated from https://github.com/gl-transitions/gl-transitions/blob/master/transitions/fade.glsl
// Author: gre
// License: MIT
// Description: A linear crossfade between the two images
// Tags: fade, blend

vec4 transition (vec2 uv) {
  return mix(
    getFromColor(uv),
    getToColor(uv),
    progress
  );
}
