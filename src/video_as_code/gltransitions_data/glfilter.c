/* frei0r.glfilter: a frei0r filter plugin that runs one GLSL ES 3.00
 * shader on a clip's picture on the GPU - EGL with no window, GLES 3 - the
 * single-clip sibling of gltransition.c. Part of video-as-code (MIT); built
 * by `video-as-code install-gl-transitions` (glfilters.py), which writes
 * the parameters:
 *
 *   shader   - path to the .glsl file: `vec4 effect(vec2 uv)`, reading the
 *              picture with getColor(uv); uniforms `time` (seconds into the
 *              clip), `progress` (0..1 over it), `ratio` (width/height) and
 *              `resolution` (pixels) are there for it
 *   progress - 0..1, keyframed by MLT over the clip ("0=0;-1=1")
 *   uniforms - the shader's own parameters, "name:v[,v...] name:v ..."
 *              (not "name=v;...": MLT reads that as keyframes)
 *
 * frei0r images are top row first; GL textures and uv bottom-up: both
 * the sampling and the output are flipped, so a read-back row 0 is the
 * top. A frame that can't be drawn (no GPU context, a shader that doesn't
 * compile) is reported on stderr and comes out black, so it shows.
 */
#include <frei0r.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    unsigned w, h;
    char *shader, *uniforms;
    double progress;
    char *built_shader;
    EGLDisplay dpy;
    EGLContext ctx;
    GLuint prog, tex[2], out_tex, fbo, vao;
    double time;
    pthread_mutex_t lock;
    int failed;
} inst_t;

int f0r_init(void) { return 1; }
void f0r_deinit(void) {}

void f0r_get_plugin_info(f0r_plugin_info_t *info) {
    info->name = "glfilter";
    info->author = "video-as-code";
    info->plugin_type = F0R_PLUGIN_TYPE_FILTER;
    info->color_model = F0R_COLOR_MODEL_RGBA8888;
    info->frei0r_version = FREI0R_MAJOR_VERSION;
    info->major_version = 1;
    info->minor_version = 0;
    info->num_params = 3;
    info->explanation = "A GLSL effect on a clip, run on the GPU";
}

void f0r_get_param_info(f0r_param_info_t *info, int i) {
    switch (i) {
    case 0: info->name = "shader"; info->type = F0R_PARAM_STRING; info->explanation = "GLSL ES 3.00 effect file"; break;
    case 1: info->name = "progress"; info->type = F0R_PARAM_DOUBLE; info->explanation = "0 to 1 over the clip"; break;
    case 2: info->name = "uniforms"; info->type = F0R_PARAM_STRING; info->explanation = "name:v[,v...] name:v ..."; break;
    }
}

f0r_instance_t f0r_construct(unsigned int w, unsigned int h) {
    inst_t *in = calloc(1, sizeof(inst_t));
    in->w = w;
    in->h = h;
    in->shader = strdup("");
    in->uniforms = strdup("");
    pthread_mutex_init(&in->lock, NULL);
    return in;
}

static void set_str(char **dst, const char *src) {
    free(*dst);
    *dst = strdup(src ? src : "");
}

void f0r_set_param_value(f0r_instance_t i, f0r_param_t p, int idx) {
    inst_t *in = i;
    pthread_mutex_lock(&in->lock);
    if (idx == 0) set_str(&in->shader, *(f0r_param_string *)p);
    else if (idx == 1) in->progress = *(double *)p;
    else if (idx == 2) set_str(&in->uniforms, *(f0r_param_string *)p);
    pthread_mutex_unlock(&in->lock);
}

void f0r_get_param_value(f0r_instance_t i, f0r_param_t p, int idx) {
    inst_t *in = i;
    if (idx == 0) *(f0r_param_string *)p = in->shader;
    else if (idx == 1) *(double *)p = in->progress;
    else if (idx == 2) *(f0r_param_string *)p = in->uniforms;
}

/* Mesa's surfaceless platform first (any GPU it drives, or llvmpipe on
 * the CPU), then the first EGL device (NVIDIA's driver), then the default */
static EGLDisplay find_display(void) {
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_display = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (get_display) {
        EGLDisplay d = get_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
        if (d != EGL_NO_DISPLAY && eglInitialize(d, NULL, NULL)) return d;
        PFNEGLQUERYDEVICESEXTPROC query_devices = (void *)eglGetProcAddress("eglQueryDevicesEXT");
        EGLDeviceEXT dev;
        EGLint n = 0;
        if (query_devices && query_devices(1, &dev, &n) && n > 0) {
            d = get_display(EGL_PLATFORM_DEVICE_EXT, dev, NULL);
            if (d != EGL_NO_DISPLAY && eglInitialize(d, NULL, NULL)) return d;
        }
    }
    EGLDisplay d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    return d != EGL_NO_DISPLAY && eglInitialize(d, NULL, NULL) ? d : EGL_NO_DISPLAY;
}

static int egl_up(inst_t *in) {
    in->dpy = find_display();
    if (in->dpy == EGL_NO_DISPLAY) return 0;
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint cfg_attrs[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_SURFACE_TYPE, 0, EGL_NONE};
    EGLConfig cfg = NULL;
    EGLint n = 0;
    eglChooseConfig(in->dpy, cfg_attrs, &cfg, 1, &n);
    EGLint ctx_attrs[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_NONE};
    /* with EGL_KHR_no_config_context, no config is needed at all */
    in->ctx = eglCreateContext(in->dpy, n > 0 ? cfg : EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ctx_attrs);
    return in->ctx != EGL_NO_CONTEXT && eglMakeCurrent(in->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, in->ctx);
}

static char *read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *s = malloc(n + 1);
    if (fread(s, 1, n, f) != (size_t)n) {
        fclose(f);
        free(s);
        return NULL;
    }
    s[n] = 0;
    fclose(f);
    return s;
}

static GLuint compile(GLenum type, const char *const *src, int count) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, count, src, NULL);
    glCompileShader(s);
    GLint ok;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[2048];
        glGetShaderInfoLog(s, sizeof log, NULL, log);
        fprintf(stderr, "frei0r.glfilter: %s\n", log);
        glDeleteShader(s);
        return 0;
    }
    return s;
}

static const char *VERT =
    "#version 300 es\n"
    "out vec2 _uv;\n"
    "void main() {\n"
    "  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));\n"
    "  _uv = p;\n"
    "  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n"
    "}\n";

static const char *FRAG_HEAD =
    "#version 300 es\n"
    "precision highp float;\n"
    "precision highp int;\n"
    "in vec2 _uv;\n"
    "out vec4 _fragColor;\n"
    "uniform sampler2D image;\n"
    "uniform float progress, ratio, time;\n"
    "uniform vec2 resolution;\n"
    "vec4 getColor(vec2 uv) { return texture(image, vec2(uv.x, 1.0 - uv.y)); }\n"
    "#line 1\n";
static const char *FRAG_TAIL = "\nvoid main() { _fragColor = effect(vec2(_uv.x, 1.0 - _uv.y)); }\n";

static int build(inst_t *in) {
    char *body = read_file(in->shader);
    if (!body) {
        fprintf(stderr, "frei0r.glfilter: can't read %s\n", in->shader);
        return 0;
    }
    const char *frag[] = {FRAG_HEAD, body, FRAG_TAIL};
    GLuint vs = compile(GL_VERTEX_SHADER, &VERT, 1), fs = compile(GL_FRAGMENT_SHADER, frag, 3);
    free(body);
    if (!vs || !fs) return 0;
    if (in->prog) glDeleteProgram(in->prog);
    in->prog = glCreateProgram();
    glAttachShader(in->prog, vs);
    glAttachShader(in->prog, fs);
    glLinkProgram(in->prog);
    glDeleteShader(vs);
    glDeleteShader(fs);
    GLint ok;
    glGetProgramiv(in->prog, GL_LINK_STATUS, &ok);
    if (!ok) {
        fprintf(stderr, "frei0r.glfilter: %s doesn't link\n", in->shader);
        return 0;
    }
    set_str(&in->built_shader, in->shader);
    return 1;
}

static void setup(inst_t *in) {
    GLuint all[3];
    glGenTextures(3, all);
    in->tex[0] = all[0];
    in->tex[1] = all[1];
    in->out_tex = all[2];
    for (int k = 0; k < 3; k++) {
        glBindTexture(GL_TEXTURE_2D, all[k]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, in->w, in->h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }
    glGenFramebuffers(1, &in->fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, in->fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, in->out_tex, 0);
    glGenVertexArrays(1, &in->vao);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
}

static GLenum uniform_type(GLuint prog, const char *wanted) {
    GLint count;
    glGetProgramiv(prog, GL_ACTIVE_UNIFORMS, &count);
    for (GLint u = 0; u < count; u++) {
        char name[256];
        GLint size;
        GLenum type;
        glGetActiveUniform(prog, u, sizeof name, NULL, &size, &type, name);
        if (!strcmp(name, wanted)) return type;
    }
    return 0;
}

static void set_uniforms(inst_t *in) {
    char *spec = strdup(in->uniforms), *save = NULL;
    for (char *item = strtok_r(spec, " ", &save); item; item = strtok_r(NULL, " ", &save)) {
        char *eq = strchr(item, ':');
        if (!eq) continue;
        *eq = 0;
        GLint loc = glGetUniformLocation(in->prog, item);
        if (loc < 0) continue; /* declared but unused: optimized away */
        float v[4] = {0};
        char *tok = eq + 1;
        for (int n = 0; n < 4 && *tok; n++) {
            v[n] = strtof(tok, &tok);
            if (*tok != ',') break;
            tok++;
        }
        switch (uniform_type(in->prog, item)) {
        case GL_FLOAT: glUniform1f(loc, v[0]); break;
        case GL_FLOAT_VEC2: glUniform2f(loc, v[0], v[1]); break;
        case GL_FLOAT_VEC3: glUniform3f(loc, v[0], v[1], v[2]); break;
        case GL_FLOAT_VEC4: glUniform4f(loc, v[0], v[1], v[2], v[3]); break;
        case GL_INT: case GL_BOOL: glUniform1i(loc, (int)v[0]); break;
        case GL_INT_VEC2: case GL_BOOL_VEC2: glUniform2i(loc, (int)v[0], (int)v[1]); break;
        case GL_INT_VEC3: case GL_BOOL_VEC3: glUniform3i(loc, (int)v[0], (int)v[1], (int)v[2]); break;
        case GL_INT_VEC4: case GL_BOOL_VEC4: glUniform4i(loc, (int)v[0], (int)v[1], (int)v[2], (int)v[3]); break;
        }
    }
    free(spec);
}

void f0r_update(f0r_instance_t i, double time, const uint32_t *in1, uint32_t *out) {
    inst_t *in = i;
    pthread_mutex_lock(&in->lock);
    if (!in->ctx && !in->failed) {
        if (egl_up(in)) setup(in);
        else {
            fprintf(stderr, "frei0r.glfilter: no EGL context with GLES 3\n");
            in->failed = 1;
        }
    }
    if (!in->failed) {
        /* this call may come on another thread than the last one */
        eglMakeCurrent(in->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, in->ctx);
        if ((!in->built_shader || strcmp(in->built_shader, in->shader)) && !build(in)) in->failed = 1;
    }
    if (in->failed) {
        memset(out, 0, (size_t)in->w * in->h * 4);
        pthread_mutex_unlock(&in->lock);
        return;
    }
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, in->tex[0]);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, in->w, in->h, GL_RGBA, GL_UNSIGNED_BYTE, in1);
    glUseProgram(in->prog);
    glUniform1i(glGetUniformLocation(in->prog, "image"), 0);
    glUniform1f(glGetUniformLocation(in->prog, "progress"), (float)in->progress);
    glUniform1f(glGetUniformLocation(in->prog, "time"), (float)time);
    glUniform1f(glGetUniformLocation(in->prog, "ratio"), (float)in->w / in->h);
    glUniform2f(glGetUniformLocation(in->prog, "resolution"), (float)in->w, (float)in->h);
    set_uniforms(in);
    glBindFramebuffer(GL_FRAMEBUFFER, in->fbo);
    glViewport(0, 0, in->w, in->h);
    glBindVertexArray(in->vao);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glReadPixels(0, 0, in->w, in->h, GL_RGBA, GL_UNSIGNED_BYTE, out);
    eglMakeCurrent(in->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    pthread_mutex_unlock(&in->lock);
}

void f0r_destruct(f0r_instance_t i) {
    inst_t *in = i;
    if (in->ctx) {
        eglMakeCurrent(in->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, in->ctx);
        if (in->prog) glDeleteProgram(in->prog);
        GLuint all[3] = {in->tex[0], in->tex[1], in->out_tex};
        glDeleteTextures(3, all);
        glDeleteFramebuffers(1, &in->fbo);
        glDeleteVertexArrays(1, &in->vao);
        eglMakeCurrent(in->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        eglDestroyContext(in->dpy, in->ctx);
    }
    free(in->shader);
    free(in->uniforms);
    free(in->built_shader);
    pthread_mutex_destroy(&in->lock);
    free(in);
}
