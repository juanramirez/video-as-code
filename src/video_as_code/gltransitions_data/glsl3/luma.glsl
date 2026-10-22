// luma — GLSL ES 3.00, generated from https://github.com/gl-transitions/gl-transitions/blob/master/transitions/luma.glsl
// Author: gre
// License: MIT
// Description: A wipe that follows the brightness of a grayscale mask (the luma texture)
// Tags: wipe, luma, mask

uniform sampler2D luma;

vec4 transition(vec2 uv) {
  return mix(
    getToColor(uv),
    getFromColor(uv),
    step(progress, texture(luma, uv).r)
  );
}
