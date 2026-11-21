// The stage: one renderer, one camera, the painted stations and the grade
// that ties them into one picture (DESIGN.md §5, §6). Everything is drawn in
// the order it was added (the layer stack), never by depth sorting: props sit
// a hair in front of their plate and must not lose to it when the camera turns.
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";

export const CAM_Z = 10;
const IMG = 16 / 9;
const VERT = "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }";

export interface Station {
  name: string;
  group: THREE.Group;
  plates: THREE.Mesh[];
}

export class Stage {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly composer: EffectComposer;
  readonly bloom: UnrealBloomPass;
  readonly grade: ShaderPass;
  private readonly loader = new THREE.TextureLoader();
  private readonly fogs: THREE.ShaderMaterial[] = [];

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.05, 400);
    this.scene.add(this.camera);
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0, 0.35, 0.93);
    this.composer.addPass(this.bloom);
    this.grade = new ShaderPass(GRADE);
    this.composer.addPass(this.grade);
    this.composer.addPass(new OutputPass());
    this.resize();
  }

  resize(): void {
    const w = innerWidth, h = innerHeight;
    this.camera.aspect = w / h;
    // Portrait screens: widen the lens so the painted frame still reads.
    this.camera.fov = w / h < 1 ? 46 : 32;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.resolution.set(w, h);
    this.grade.uniforms.uRes.value.set(w, h);
  }

  /** A plate's size so it still covers the view at its depth through the camera's travel. */
  fit(z: number, over = 1.2): { w: number; h: number } {
    const h = 2 * (CAM_Z - z) * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * over;
    const w = Math.max(h * IMG, h * this.camera.aspect);
    return { w, h: w / IMG };
  }

  texture(url: string): Promise<THREE.Texture | null> {
    return new Promise((res) =>
      this.loader.load(
        url,
        (t) => {
          t.colorSpace = THREE.SRGBColorSpace;
          t.anisotropy = 8;
          res(t);
        },
        undefined,
        () => res(null),
      ),
    );
  }

  /** A station: its plates at their depths, back to front. */
  async station(name: string, layers: [url: string, z: number, over?: number][]): Promise<Station> {
    const group = new THREE.Group();
    group.visible = false;
    this.scene.add(group);
    const maps = await Promise.all(layers.map(([url]) => this.texture(url)));
    const plates = layers.map(([, z, over], i) => {
      const { w, h } = this.fit(z, over);
      const m = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ map: maps[i], transparent: true, depthWrite: false }),
      );
      m.position.z = z;
      if (!maps[i]) m.visible = false;
      group.add(m);
      return m;
    });
    return { name, group, plates };
  }

  /** A point on a plate, as fractions of its frame (u right, v down). */
  at(plate: THREE.Mesh, u: number, v: number, dz = 0): THREE.Vector3 {
    const g = plate.geometry as THREE.PlaneGeometry;
    return new THREE.Vector3((u - 0.5) * g.parameters.width, (0.5 - v) * g.parameters.height, plate.position.z + dz);
  }

  /** A drifting band of mist, moving on its own while on screen. */
  fog(group: THREE.Object3D, z: number, strength: number, color: string, band: number): THREE.Mesh {
    const { w, h } = this.fit(z, 1.3);
    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      uniforms: { uTime: { value: 0 }, uStrength: { value: strength }, uColor: { value: new THREE.Color(color) }, uBand: { value: band } },
      vertexShader: VERT,
      fragmentShader: FOG,
    });
    this.fogs.push(mat);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
    m.position.z = z;
    group.add(m);
    return m;
  }

  /** Fix the draw order of a group to its children's order; `last` draw after everything. */
  order(group: THREE.Object3D, base = 0, last: THREE.Object3D[] = []): void {
    group.children.forEach((o, i) => o.traverse((c) => (c.renderOrder = base + i)));
    for (const o of last) o.traverse((c) => (c.renderOrder = base + 900));
  }

  render(t: number): void {
    for (const f of this.fogs) f.uniforms.uTime.value = t;
    this.grade.uniforms.uTime.value = t;
    this.composer.render();
  }
}

export const radial = (rgb: string): THREE.CanvasTexture => {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  const r = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  r.addColorStop(0, `rgba(${rgb},1)`);
  r.addColorStop(0.35, `rgba(${rgb},.45)`);
  r.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = r;
  g.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
};

const FOG = `varying vec2 vUv; uniform float uTime,uStrength,uBand; uniform vec3 uColor;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p*=2.03;a*=.5;}return v;}
void main(){ vec2 p=vUv*vec2(3.,1.4)+vec2(uTime*.02,0.); float f=fbm(p+fbm(p*.6+uTime*.012));
  gl_FragColor=vec4(uColor,smoothstep(.35,.8,f)*exp(-pow((vUv.y-uBand)/.16,2.))*uStrength); }`;

// One grade over the whole stage: the light of the hour, the rewind's tape,
// the soulbound split, a vignette and grain.
const GRADE = {
  uniforms: {
    tDiffuse: { value: null },
    tAlt: { value: null },
    uTime: { value: 0 },
    uWarm: { value: 0 },
    uCool: { value: 0 },
    uNight: { value: 0 },
    uRewind: { value: 0 },
    uSplit: { value: -1 },
    uFlash: { value: 0 },
    uRes: { value: new THREE.Vector2(1, 1) },
  },
  vertexShader: VERT,
  fragmentShader: `varying vec2 vUv; uniform sampler2D tDiffuse,tAlt; uniform float uTime,uWarm,uCool,uNight,uRewind,uSplit,uFlash; uniform vec2 uRes;
float h(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
void main(){
  vec2 uv=vUv;
  // rewind: the picture tears, slips and loses its colour, as tape running back
  float band=step(.985,h(vec2(floor(uv.y*90.+uTime*40.),floor(uTime*24.))))*uRewind;
  uv.x+=band*.04+sin(uv.y*400.+uTime*60.)*.0015*uRewind;
  vec3 c=texture2D(tDiffuse,uv).rgb;
  if(uSplit>=0.&&vUv.x<uSplit){ c=texture2D(tAlt,uv).rgb; }
  float l=dot(c,vec3(.2126,.7152,.0722));
  c=mix(c,vec3(l)*vec3(.92,.96,1.05),uRewind*.75);
  c+=vec3(.08,.09,.12)*band*2.;
  c=mix(c,c*vec3(.92,.96,1.08)+vec3(.006,.01,.03)*(1.-l),.6*uCool);
  c=mix(c,c*vec3(1.05,1.,.93),uWarm*.35);
  c=mix(c,c*vec3(.78,.84,1.)*.9,uNight*.35);
  if(uSplit>=0.){ float e=abs(vUv.x-uSplit)*uRes.x; c=mix(vec3(.97,.69,.52),c,smoothstep(.0,1.6,e)); }
  c*=mix(.72,1.,smoothstep(1.15,.35,length((vUv-.5)*vec2(uRes.x/uRes.y,1.)*.9)));
  c+=(h(vUv*uRes+fract(uTime)*91.)-.5)*(.03+uRewind*.08);
  c=mix(c,vec3(1.,.97,.92),uFlash);
  gl_FragColor=vec4(c,1.); }`,
};
