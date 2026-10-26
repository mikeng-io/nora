// Her body: one WebGL2 canvas that draws a pose of her render, warped a
// little on a mesh (breath, a head roll, hair that lags, a limb that moves),
// with her eyes and mouth drawn on top as shapes rather than pixels, so they
// can blink, look, and make the expressions of her character sheet.
//
// The textures are her renders with the eyes and mouth painted out
// (site/scripts/make_rig.py); the numbers that say where everything is are
// rig.json. There is one canvas because there is one of her: the stage
// (stage.ts) moves it to wherever she is on the page.
import type { Frame, PoseId, PoseRig } from "./types";

const VERT = `#version 300 es
precision highp float;
in vec2 a_uv;
uniform float u_aspect;
uniform vec4 u_dest;
uniform vec4 u_head;
uniform vec2 u_neck;
uniform vec4 u_face;
uniform vec3 u_bun;
uniform vec2 u_hips;
uniform float u_ground;
uniform float u_roll;
uniform vec2 u_turn;
uniform vec2 u_nod;
uniform float u_breath;
uniform float u_sway;
uniform vec2 u_hairLag;
uniform float u_hairDrift;
uniform vec2 u_squash;
uniform float u_time;
uniform vec4 u_limbRegion[3];
uniform vec3 u_limbPivot[3];
uniform vec2 u_limbShift[3];
uniform int u_limbs;
out vec2 v_uv;

vec2 A(vec2 uv) { return vec2(uv.x * u_aspect, uv.y); }
float ell(vec2 p, vec4 e) { return length((p - vec2(e.x * u_aspect, e.y)) / e.zw); }
vec2 rot(vec2 p, vec2 o, float a) {
  float c = cos(a), s = sin(a);
  vec2 d = p - o;
  return o + vec2(c * d.x - s * d.y, s * d.x + c * d.y);
}

void main() {
  vec2 p0 = A(a_uv);
  vec2 p = p0;

  for (int i = 0; i < 3; i++) {
    if (i >= u_limbs) break;
    float w = 1.0 - smoothstep(0.7, 1.55, ell(p0, u_limbRegion[i]));
    p = rot(p, A(u_limbPivot[i].xy), u_limbPivot[i].z * w) + u_limbShift[i] * w;
  }

  // her face features move a little more than her head when she turns it
  float wFace = 1.0 - smoothstep(0.5, 1.25, ell(p0, u_face));
  p += u_turn * wFace;

  // hair: the strands that hang from her head, longer ones more, and the bun
  float wHead = 1.0 - smoothstep(0.86, 1.22, ell(p0, u_head));
  float off = smoothstep(0.95, 1.7, ell(p0, u_face));
  float low = smoothstep(u_head.y - 0.2 * u_head.w, u_head.y + u_head.w, p0.y);
  float wBun = 1.0 - smoothstep(0.45, 1.3, length(p0 - vec2(u_bun.x * u_aspect, u_bun.y)) / u_bun.z);
  float tip = max(wHead * off * (0.3 + 0.7 * low), wBun * 0.85);
  float drift = sin(u_time * 1.21 + p0.y * 9.0 + p0.x * 2.0) * 0.6
              + sin(u_time * 0.73 + p0.y * 17.0 - p0.x * 6.0) * 0.4;
  p.x += drift * u_hairDrift * tip;
  p += u_hairLag * tip;

  // the head nods, droops and rolls about the neck
  p += u_nod * wHead;
  p = rot(p, A(u_neck), u_roll * wHead);

  // breath lifts the torso from the hips; everything above the neck rides it
  float lift = clamp((u_hips.y - p0.y) / max(u_hips.y - u_neck.y, 1e-3), 0.0, 1.0);
  p.y -= u_breath * lift;
  float chest = lift * (1.0 - wHead);
  p.x += (p0.x - u_hips.x * u_aspect) * u_breath * 2.2 * chest;

  // a weight shift leans her about the ground, feet planted
  vec2 g = vec2(u_hips.x * u_aspect, u_ground);
  p = rot(p, g, u_sway * (1.0 - smoothstep(u_hips.y, u_ground, p0.y)));
  p = g + (p - g) * u_squash;

  v_uv = a_uv;
  vec2 uv = vec2(p.x / u_aspect, p.y);
  gl_Position = vec4(mix(u_dest.xy, u_dest.zw, uv), 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_aspect;
uniform float u_alpha;
uniform vec4 u_eye[2];
uniform float u_eyeRot[2];
uniform vec3 u_eyeCore[2];
uniform vec3 u_eyeRim[2];
uniform int u_eyes;
uniform vec2 u_gaze;
uniform vec2 u_eyeScale;
uniform float u_lid;
uniform float u_flat;
uniform float u_tilt;
uniform float u_lash;
uniform float u_low;
uniform vec4 u_mouth;
uniform vec3 u_mouthColour;
uniform float u_mouthO;
uniform float u_smile;
uniform float u_blush;
uniform vec4 u_featBox;
out vec4 o;

// Antialiasing widths come from the size of a pixel in texture space, taken
// once in main(): the face is only shaded near the face, and a derivative
// taken inside that branch would be undefined where a pixel quad straddles it.
float px;

// One eye, premultiplied. Its lids are edges that sweep across it: the upper
// one curved like the lash line of her closed eyes (so fully closed leaves
// exactly her closed crescent), or flat and tilted for a sleepy or an
// unimpressed look; the lower one rising for the ^ ^ of her happy eyes.
vec4 eye(vec4 e, float rotE, vec3 core, vec3 rim, float outer, vec2 q) {
  // The eye opening stays planted in her face; gaze moves iris and pupil.
  vec2 c = vec2(e.x * u_aspect, e.y);
  float rx = max(e.z * u_eyeScale.x, 1e-5);
  float ry = max(e.w * u_eyeScale.y, 1e-5);
  vec2 d = q - c;
  float cs = cos(rotE), sn = sin(rotE);
  vec2 l = vec2(cs * d.x + sn * d.y, -sn * d.x + cs * d.y);
  vec2 n = l / vec2(rx, ry);
  float fy = max(px / ry, 1e-4);
  float fx = max(px / rx, 1e-4);
  float span = sqrt(max(0.0, 1.0 - n.x * n.x));
  float validX = 1.0 - smoothstep(1.0 - fx, 1.0 + fx, abs(n.x));

  // Balanced: rounded anime opening, warm sclera and one upper line.
  // At closure the upper line reaches the lower edge; no iris remains.
  float naturalTop = -span;
  float closedTop = 0.38 - 0.38 * n.x * n.x;
  float curved = mix(naturalTop, closedTop, u_lid);
  float flatLid = mix(-1.1, 0.38, u_lid) + tan(u_tilt) * outer * n.x * (rx / ry);
  float edge = mix(curved, max(naturalTop, flatLid), u_flat);
  float bottom = mix(span, -0.38 + 0.38 * n.x * n.x, u_low);
  // Happy closure brings the upper edge onto the lower arch: one lid line.
  edge = mix(edge, bottom, u_low);
  float below = smoothstep(edge - fy, edge + fy, n.y);
  float above = 1.0 - smoothstep(bottom - fy, bottom + fy, n.y);
  float opening = validX * below * above;
  opening *= 1.0 - smoothstep(0.92, 1.0, max(u_lid, u_low));

  vec2 irisPos = vec2(u_gaze.x * 0.30, u_gaze.y * 0.23 + 0.04);
  vec2 irisN = (n - irisPos) / vec2(0.66, 1.02);
  float irisR = length(irisN);
  float irisA = 1.0 - smoothstep(1.0 - fy, 1.0 + fy, irisR);
  // Brown iris with a warmer lower half and dark limbal rim.
  vec3 iris = mix(core, rim, smoothstep(-0.6, 0.85, irisN.y));
  iris = mix(iris, core * 0.65, smoothstep(0.84, 1.0, irisR));
  float pupilR = length(irisN / vec2(0.37, 0.54));
  float pupil = 1.0 - smoothstep(0.94, 1.06, pupilR);
  iris = mix(iris, core * 0.40, pupil);
  float glint = 1.0 - smoothstep(0.15, 0.19, length(irisN - vec2(0.24, -0.48)));
  float smallGlint = 1.0 - smoothstep(0.045, 0.075, length(irisN - vec2(-0.30, 0.35)));
  iris = mix(iris, vec3(1.0, 0.98, 0.94), max(glint, smallGlint * 0.65));
  vec3 sclera = vec3(0.985, 0.966, 0.93) * (0.94 + 0.06 * smoothstep(-0.6, 0.5, n.y));
  vec3 col = mix(sclera, iris, irisA);

  // Continuous lid line for open, blink, happy, sleepy and lidded states.
  float lineWidth = mix(0.075, 0.055, u_lid) + u_lash * 0.025;
  float topLine = validX * (1.0 - smoothstep(lineWidth, lineWidth + fy * 1.5, abs(n.y - edge)));
  float along = n.x * outer;
  float wingY = -0.13 - (along - 0.85) * 0.65;
  float wing = (1.0 - smoothstep(0.055, 0.055 + fy, abs(n.y - wingY)))
             * smoothstep(0.82, 0.94, along) * (1.0 - smoothstep(1.12, 1.28, along))
             * (1.0 - max(u_lid, u_low));
  float lineA = max(topLine, wing);
  float a = max(opening, lineA);
  col = mix(col, core * 0.55, lineA);

  return vec4(col * a, a);
}

vec4 mouth(vec2 q) {
  vec2 c = vec2(u_mouth.x * u_aspect, u_mouth.y);
  float w = u_mouth.z, h = max(u_mouth.w, 1e-4);
  vec2 d = q - c;
  float L = max(w * 0.5 - h * 0.5, 1e-4);
  float t = clamp(d.x / L, -1.0, 1.0);
  float bend = u_smile * h * 1.3 * (0.5 - t * t);
  float line = length(vec2(d.x - clamp(d.x, -L, L), d.y - bend)) - h * 0.5;
  float ow = h * (1.0 + 0.45 * u_mouthO), oh = h * (0.6 + 1.6 * u_mouthO);
  float oval = (length(d / vec2(ow, oh)) - 1.0) * min(ow, oh);
  float dist = mix(line, oval, smoothstep(0.05, 0.4, u_mouthO));
  float f = max(px, 1e-5);
  float a = 1.0 - smoothstep(-f, f, dist);
  vec3 col = mix(u_mouthColour, u_mouthColour * vec3(1.12, 0.86, 0.84), smoothstep(0.2, 1.0, u_mouthO));
  return vec4(col * a, a);
}

void main() {
  vec4 base = texture(u_tex, v_uv);
  vec2 q = vec2(v_uv.x * u_aspect, v_uv.y);
  px = max(length(dFdx(q)), length(dFdy(q)));
  // everything below is her face; most of the canvas is not
  if (any(lessThan(q, u_featBox.xy)) || any(greaterThan(q, u_featBox.zw))) {
    o = base * u_alpha;
    return;
  }
  if (u_blush > 0.0 && u_eyes > 0) {
    float g = 0.0;
    for (int i = 0; i < 2; i++) {
      vec4 e = u_eye[i];
      float side = i == 0 ? -1.0 : 1.0;
      vec2 cc = vec2(e.x * u_aspect + side * e.z * 0.35, e.y + e.w * 1.55);
      g += exp(-dot((q - cc) / vec2(e.z * 1.25, e.w * 0.75), (q - cc) / vec2(e.z * 1.25, e.w * 0.75)));
    }
    base.rgb = mix(base.rgb, vec3(0.93, 0.52, 0.47) * base.a, clamp(g, 0.0, 1.0) * 0.32 * u_blush);
  }
  vec4 f = vec4(0.0);
  if (u_eyes > 0) {
    vec4 e0 = eye(u_eye[0], u_eyeRot[0], u_eyeCore[0], u_eyeRim[0], -1.0, q);
    vec4 e1 = eye(u_eye[1], u_eyeRot[1], u_eyeCore[1], u_eyeRim[1], 1.0, q);
    f = e0 + e1 * (1.0 - e0.a);
  }
  vec4 m = mouth(q);
  f = f + m * (1.0 - f.a);
  f *= base.a;
  o = (f + base * (1.0 - f.a)) * u_alpha;
}`;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Loaded {
  tex: WebGLTexture;
  rig: PoseRig;
}

export class Renderer {
  readonly canvas: HTMLCanvasElement;
  private gl: WebGL2RenderingContext;
  private prog: WebGLProgram;
  private vao: WebGLVertexArrayObject;
  private count: number;
  private u = new Map<string, WebGLUniformLocation | null>();
  private poses = new Map<PoseId, Loaded>();
  private pending = new Map<PoseId, Promise<void>>();
  private dpr = 1;
  private onContextLost: (e: Event) => void;
  lost = false;

  constructor(onLost: () => void) {
    this.canvas = document.createElement("canvas");
    this.canvas.className = "nora-body";
    this.canvas.setAttribute("aria-hidden", "true");
    const gl = this.canvas.getContext("webgl2", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
    });
    if (!gl) throw new Error("no webgl2");
    this.gl = gl;
    this.onContextLost = (e: Event) => {
      e.preventDefault();
      this.lost = true;
      onLost();
    };
    this.canvas.addEventListener("webglcontextlost", this.onContextLost);
    this.prog = this.program(VERT, FRAG);
    const cols = 40;
    const rows = 64;
    const verts: number[] = [];
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const quad = [
          [x, y],
          [x + 1, y],
          [x, y + 1],
          [x + 1, y],
          [x + 1, y + 1],
          [x, y + 1],
        ];
        for (const [qx, qy] of quad) verts.push(qx / cols, qy / rows);
      }
    }
    this.count = verts.length / 2;
    this.vao = gl.createVertexArray()!;
    gl.bindVertexArray(this.vao);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(this.prog, "a_uv");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  }

  private program(vs: string, fs: string): WebGLProgram {
    const gl = this.gl;
    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "shader");
      return s;
    };
    const p = gl.createProgram()!;
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? "link");
    return p;
  }

  private loc(name: string): WebGLUniformLocation | null {
    if (!this.u.has(name)) this.u.set(name, this.gl.getUniformLocation(this.prog, name));
    return this.u.get(name)!;
  }

  /** Lets the GPU context go, without reporting it as lost. */
  dispose(): void {
    this.canvas.removeEventListener("webglcontextlost", this.onContextLost);
    this.lost = true;
    this.gl.getExtension("WEBGL_lose_context")?.loseContext();
  }

  has(pose: PoseId): boolean {
    return this.poses.has(pose);
  }

  /**
   * Loads a pose's texture once; later calls share the same promise.
   *
   * Decoded and premultiplied off the main thread (createImageBitmap), and
   * handed to the GPU only when `quiet` says the page is not in the middle
   * of something (a scroll): an upload is the one step here that can hold
   * the main thread for tens of milliseconds.
   */
  load(pose: PoseId, url: string, rig: PoseRig, quiet: () => Promise<void> = async () => undefined): Promise<void> {
    const known = this.pending.get(pose);
    if (known) return known;
    const job = (async () => {
      let source: TexImageSource;
      let premultiplied = false;
      if ("createImageBitmap" in window) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`texture ${pose}: HTTP ${res.status}`);
        const blob = await res.blob();
        source = await createImageBitmap(blob, { premultiplyAlpha: "premultiply" });
        premultiplied = true;
      } else {
        const img = new Image();
        img.src = url;
        await img.decode();
        source = img;
      }
      await quiet();
      const gl = this.gl;
      const tex = gl.createTexture()!;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, !premultiplied);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      if (source instanceof ImageBitmap) source.close();
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.poses.set(pose, { tex, rig });
    })();
    this.pending.set(pose, job);
    job.catch(() => this.pending.delete(pose));
    return job;
  }

  private boxes = new Map<PoseId, [number, number, number, number]>();

  /** Where her face can reach in texture space (aspect-corrected x): both eyes
      with room for a look, a widened close and the lash line, the cheeks her
      blush reaches, and the mouth open. Outside it the shader does nothing. */
  private faceBox(pose: PoseId, r: PoseRig): [number, number, number, number] {
    const known = this.boxes.get(pose);
    if (known) return known;
    const a = r.size[0] / r.size[1];
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const e of r.eyes) {
      x0 = Math.min(x0, e.cx * a - e.rx * 2.4);
      x1 = Math.max(x1, e.cx * a + e.rx * 2.4);
      y0 = Math.min(y0, e.cy - e.ry * 1.8);
      y1 = Math.max(y1, e.cy + e.ry * 3.6);
    }
    const m = r.mouth;
    x0 = Math.min(x0, m.cx * a - m.w - m.h * 2);
    x1 = Math.max(x1, m.cx * a + m.w + m.h * 2);
    y0 = Math.min(y0, m.cy - m.h * 5);
    y1 = Math.max(y1, m.cy + m.h * 5);
    const box: [number, number, number, number] = [x0, y0, x1, y1];
    this.boxes.set(pose, box);
    return box;
  }

  /** Sizes the drawing buffer to the canvas's CSS box; cheap when unchanged. */
  resize(cssW: number, cssH: number): void {
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(cssW * this.dpr));
    const h = Math.max(1, Math.round(cssH * this.dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    this.gl.viewport(0, 0, w, h);
  }

  clear(): void {
    const gl = this.gl;
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }

  /** Draws `pose` into `dest` (CSS px inside the canvas) as frame `f`. */
  draw(pose: PoseId, f: Frame, dest: Rect, alpha = 1): void {
    const p = this.poses.get(pose);
    if (!p || alpha <= 0.001) return;
    const gl = this.gl;
    const r = p.rig;
    const W = this.canvas.width / this.dpr;
    const H = this.canvas.height / this.dpr;
    gl.useProgram(this.prog);
    gl.bindVertexArray(this.vao);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, p.tex);
    const u1 = (n: string, v: number) => gl.uniform1f(this.loc(n), v);
    const u2 = (n: string, a: number, b: number) => gl.uniform2f(this.loc(n), a, b);
    const u3 = (n: string, a: number, b: number, c: number) => gl.uniform3f(this.loc(n), a, b, c);
    const u4 = (n: string, a: number, b: number, c: number, d: number) => gl.uniform4f(this.loc(n), a, b, c, d);
    gl.uniform1i(this.loc("u_tex"), 0);
    u1("u_aspect", r.size[0] / r.size[1]);
    u4(
      "u_dest",
      (dest.x / W) * 2 - 1,
      1 - (dest.y / H) * 2,
      ((dest.x + dest.w) / W) * 2 - 1,
      1 - ((dest.y + dest.h) / H) * 2,
    );
    u4("u_head", r.head.cx, r.head.cy, r.head.rx, r.head.ry);
    u2("u_neck", r.neck[0], r.neck[1]);
    u4("u_face", r.face.cx, r.face.cy, r.face.rx, r.face.ry);
    u3("u_bun", r.bun.cx, r.bun.cy, r.bun.r);
    u2("u_hips", r.hips[0], r.hips[1]);
    u1("u_ground", r.ground);
    u1("u_roll", f.roll);
    u2("u_turn", f.turn[0], f.turn[1]);
    u2("u_nod", f.nod[0], f.nod[1]);
    u1("u_breath", f.breath);
    u1("u_sway", f.sway);
    u2("u_hairLag", f.hairLag[0], f.hairLag[1]);
    u1("u_hairDrift", f.hairDrift);
    u2("u_squash", f.squash[0], f.squash[1]);
    u1("u_time", f.time);
    const n = Math.min(r.limbs.length, 3);
    gl.uniform1i(this.loc("u_limbs"), n);
    for (let i = 0; i < n; i++) {
      const l = r.limbs[i];
      u4(`u_limbRegion[${i}]`, l.region.cx, l.region.cy, l.region.rx, l.region.ry);
      u3(`u_limbPivot[${i}]`, l.pivot[0], l.pivot[1], f.limbRot[i] ?? 0);
      const s = f.limbShift[i] ?? [0, 0];
      u2(`u_limbShift[${i}]`, s[0], s[1]);
    }
    const eyes = r.eyes.length >= 2 ? 2 : 0;
    gl.uniform1i(this.loc("u_eyes"), eyes);
    for (let i = 0; i < eyes; i++) {
      const e = r.eyes[i];
      u4(`u_eye[${i}]`, e.cx, e.cy, e.rx, e.ry);
      u1(`u_eyeRot[${i}]`, e.rot);
      u3(`u_eyeCore[${i}]`, ...e.core);
      u3(`u_eyeRim[${i}]`, ...e.rim);
    }
    u2("u_gaze", f.gaze[0], f.gaze[1]);
    u2("u_eyeScale", f.eyeScale[0], f.eyeScale[1]);
    u1("u_lid", f.lid);
    u1("u_flat", f.flat);
    u1("u_tilt", f.tilt);
    u1("u_lash", f.lash);
    u1("u_low", f.low);
    u4("u_mouth", r.mouth.cx, r.mouth.cy, r.mouth.w, r.mouth.h);
    u3("u_mouthColour", ...r.mouth.colour);
    u1("u_mouthO", f.mouthO);
    u1("u_smile", f.smile);
    u1("u_blush", f.blush);
    u1("u_alpha", alpha);
    const box = this.faceBox(pose, r);
    u4("u_featBox", box[0], box[1], box[2], box[3]);
    gl.drawArrays(gl.TRIANGLES, 0, this.count);
  }
}
