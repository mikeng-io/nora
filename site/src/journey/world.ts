// The painted world: eight stations of depth plates (scripts/make_journey.py),
// and the things the story moves through them. Motifs keep one meaning each
// (DESIGN.md §8): a slip is something said, a thread is a connection (pale
// for understanding, copper for what will happen), a lantern is a promise.
import * as THREE from "three";
import meta from "../assets/journey/meta.json";
import { Stage, radial, type Station } from "./stage";

const urls = import.meta.glob("../assets/journey/**/*.webp", { query: "?url", import: "default", eager: true }) as Record<string, string>;
const art = (path: string) => urls[`../assets/journey/${path}.webp`] ?? "";

export const COPPER = new THREE.Color("#e3906a");
export const PALE = new THREE.Color("#9db8e6");

type Layers = [string, number, number?][];
const LAYERS: Record<string, Layers> = {
  hero: [["01-sky", -90], ["02-far", -60], ["03-mid", -30], ["04-near", -9], ["05-foreground", -3.2, 1.25]],
  circles: [["01-sky", -90], ["02-far", -60], ["03-land", -14], ["04-mist", -10], ["05-foreground", -3.2, 1.25]],
  memory: [["01-sky", -90], ["02-far", -50], ["03-cliff", -14], ["04-slips", -12.5], ["05-foreground", -3.2, 1.25]],
  ridge: [["01-sky", -90], ["02-cloudsea", -40], ["03-ridge", -9], ["04-foreground", -3.2, 1.25]],
  path: [["01-sky", -90], ["02-far-peaks", -60], ["03-mid-peaks", -30], ["04-path", -9], ["05-foreground", -3.2, 1.22]],
  terrace: [["01-sky", -90], ["02-far", -45], ["03-terrace", -9], ["04-foreground", -3.2, 1.25]],
  gate: [["01-sky", -90], ["02-far", -55], ["03-wall", -12], ["04-steps", -8], ["05-foreground", -3.2, 1.25]],
};

/** A paper slip with words on it, drawn over the painted paper. */
function slip(paper: THREE.Texture | null, text: string): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 900;
  c.height = 252;
  const g = c.getContext("2d")!;
  const im = paper?.image as HTMLImageElement | undefined;
  if (im) {
    const sw = im.width * 0.86, sh = sw / 3.87;
    g.drawImage(im, (im.width - sw) / 2, (im.height - sh) / 2, sw, sh, 0, 0, 900, 252);
  } else {
    g.fillStyle = "#f5eee1";
    g.beginPath();
    g.roundRect(14, 14, 872, 224, 12);
    g.fill();
  }
  g.fillStyle = "#1a2134";
  g.font = '500 40px "Inter", sans-serif';
  let line = "", y = 112;
  for (const w of text.split(" ")) {
    const t = line + w + " ";
    if (g.measureText(t).width > 700 && line) {
      g.fillText(line, 120, y);
      line = w + " ";
      y += 50;
    } else line = t;
  }
  g.fillText(line, 120, y);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function thread(from: THREE.Vector3, to: THREE.Vector3, lift: number, width: number): THREE.Mesh {
  const mid = from.clone().lerp(to, 0.5).add(new THREE.Vector3(0, lift, 0.2));
  return new THREE.Mesh(
    new THREE.TubeGeometry(new THREE.CatmullRomCurve3([from, mid, to]), 80, width, 6, false),
    new THREE.MeshBasicMaterial({ color: PALE.clone(), transparent: true, opacity: 0.5 }),
  );
}

export class Lantern {
  readonly group = new THREE.Group();
  private readonly lit: THREE.Mesh;
  private readonly spill: THREE.Mesh;
  constructor(unlit: THREE.Texture | null, litMap: THREE.Texture | null, size: number) {
    const cool = new THREE.MeshBasicMaterial({ map: unlit, transparent: true, depthWrite: false, color: new THREE.Color(0.55, 0.6, 0.78) });
    this.group.add(new THREE.Mesh(new THREE.PlaneGeometry(size, size), cool));
    this.lit = new THREE.Mesh(new THREE.PlaneGeometry(size, size), new THREE.MeshBasicMaterial({ map: litMap, transparent: true, depthWrite: false, opacity: 0 }));
    this.spill = new THREE.Mesh(
      new THREE.PlaneGeometry(size * 4.5, size * 3.4),
      new THREE.MeshBasicMaterial({ map: radial("255,190,120"), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0 }),
    );
    this.spill.position.set(0, -size * 0.35, 0.01);
    this.group.add(this.lit, this.spill);
  }
  set(k: number, t: number): void {
    (this.lit.material as THREE.MeshBasicMaterial).opacity = k;
    (this.spill.material as THREE.MeshBasicMaterial).opacity = 0.55 * k * (0.93 + 0.07 * Math.sin(t * 7.3) * Math.sin(t * 2.9));
    this.group.rotation.z = Math.sin(t * 0.8 + this.group.position.x) * 0.012;
  }
}

export class World {
  stations = new Map<string, Station>();
  night: THREE.Mesh[] = [];
  lantern!: Lantern;
  rail: Lantern[] = [];
  threads: THREE.Mesh[] = [];
  traveller!: THREE.Mesh;
  travelPath!: THREE.CatmullRomCurve3;
  stars: { mesh: THREE.Mesh; from: THREE.Vector3; to: THREE.Vector3 }[] = [];
  starLinks!: THREE.LineSegments;
  falling: { mesh: THREE.Mesh; x: number; speed: number; phase: number }[] = [];
  lifted!: THREE.Mesh;
  liftFrom = new THREE.Vector3();
  pavilionLights: THREE.Mesh[] = [];
  pavilionLanterns: THREE.Mesh[] = [];
  transit = { warm: [] as (THREE.Texture | null)[], cool: [] as (THREE.Texture | null)[] };
  maps: Record<string, THREE.Texture | null> = {};

  constructor(
    private readonly stage: Stage,
    private readonly words: string[],
  ) {}

  station(name: string): Station {
    return this.stations.get(name)!;
  }

  /** The hero first, so the page can start; the rest arrive behind it. */
  async loadHero(): Promise<void> {
    await this.build("hero");
    const [a, b, c, d] = await Promise.all(["cloud-a", "cloud-b", "fog-a", "fog-b"].map((n) => this.stage.texture(art(`transit/${n}`))));
    this.transit = { warm: [a, b], cool: [c, d] };
  }

  async loadRest(): Promise<void> {
    for (const n of ["unlit", "lit"]) this.maps[n] = await this.stage.texture(art(`props/lantern-${n}`));
    this.maps.slip = await this.stage.texture(art("props/slip"));
    await Promise.all(["circles", "memory", "ridge", "path", "terrace", "gate"].map((n) => this.build(n)));
    await this.dressPath();
    this.dressMemory();
    this.dressRidge();
    this.dressCircles();
    for (const s of this.stations.values()) this.stage.order(s.group, 0, [s.plates[s.plates.length - 1]]);
  }

  private async build(name: string): Promise<void> {
    const s = await this.stage.station(name, LAYERS[name].map(([f, z, o]) => [art(`${name}/${f}`), z, o] as [string, number, number?]));
    this.stage.fog(s.group, -22, name === "memory" || name === "ridge" ? 0.42 : 0.3, name === "hero" ? "#f1d9cc" : "#aebcd8", 0.55);
    this.stations.set(name, s);
  }

  /** The path at blue hour, its night twin laid over it, the lanterns, the slips and their threads. */
  private async dressPath(): Promise<void> {
    const s = this.station("path");
    const nightFiles = LAYERS.path.map(([f]) => f);
    const maps = await Promise.all(nightFiles.map((f) => this.stage.texture(art(`night/${f}`))));
    // each night plate draws right after its day plate, so night can fade in layer by layer
    const order = [...s.group.children];
    s.group.clear();
    for (const o of order) {
      s.group.add(o);
      const i = s.plates.indexOf(o as THREE.Mesh);
      if (i < 0) continue;
      const m = new THREE.Mesh(s.plates[i].geometry, new THREE.MeshBasicMaterial({ map: maps[i], transparent: true, depthWrite: false, opacity: 0 }));
      m.position.copy(s.plates[i].position);
      m.position.z += 0.001;
      s.group.add(m);
      this.night.push(m);
    }
    const pp = s.plates[3];
    const LS = (pp.geometry as THREE.PlaneGeometry).parameters.height * 0.17;
    this.lantern = new Lantern(this.maps.unlit, this.maps.lit, LS);
    this.lantern.group.position.copy(this.stage.at(pp, 0.452, 0.7, 0.02)).add(new THREE.Vector3(-0.32 * LS, -0.3 * LS, 0));
    s.group.add(this.lantern.group);
    // the rest of the rail, for the soulbound beat: smaller with distance
    const posts: [number, number, number][] = [[0.635, 0.69, 0.78], [0.775, 0.62, 0.6], [0.868, 0.565, 0.47], [0.937, 0.5, 0.38]];
    for (const [u, v, k] of posts) {
      const l = new Lantern(this.maps.unlit, this.maps.lit, LS * k);
      l.group.position.copy(this.stage.at(pp, u, v, 0.02)).add(new THREE.Vector3(-0.32 * LS * k, -0.3 * LS * k, 0));
      l.group.visible = false;
      s.group.add(l.group);
      this.rail.push(l);
    }
    const lines = ["Alex: someone should sort this before Friday", "Sam: I can do Thursday too", "Alex: ok. I’ll book"];
    const at: [number, number][] = [[0.67, 0.22], [0.6, 0.31], [0.71, 0.4]];
    lines.forEach((text, i) => {
      const p = this.stage.at(pp, at[i][0], at[i][1], 0.05 + i * 0.03);
      const m = new THREE.Mesh(new THREE.PlaneGeometry(LS * 1.5, LS * 0.42), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, text), transparent: true, depthWrite: false, color: new THREE.Color(0.8, 0.82, 0.9) }));
      m.position.copy(p);
      m.rotation.z = (i - 1) * 0.05;
      s.group.add(m);
      const t = thread(p.clone().add(new THREE.Vector3(-LS * 0.7, 0, 0)), this.lantern.group.position.clone().add(new THREE.Vector3(0, LS * 0.1, 0)), LS * (0.4 - i * 0.25), LS * 0.012);
      s.group.add(t);
      this.threads.push(t);
    });
    // the slip that comes in the night, from another conversation
    this.traveller = new THREE.Mesh(new THREE.PlaneGeometry(LS * 1.5, LS * 0.42), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, "Mia: can we do Thursday instead?"), transparent: true, depthWrite: false, opacity: 0 }));
    const end = this.lantern.group.position.clone().add(new THREE.Vector3(LS * 0.9, LS * 0.35, 0.1));
    this.travelPath = new THREE.CatmullRomCurve3([this.stage.at(pp, 1.15, 0.12, 1.5), this.stage.at(pp, 0.85, 0.25, 0.8), this.stage.at(pp, 0.62, 0.42, 0.3), end]);
    s.group.add(this.traveller);
    const tail = thread(this.travelPath.points[0], end, 0, LS * 0.01);
    tail.geometry = new THREE.TubeGeometry(this.travelPath, 120, LS * 0.01, 6, false);
    tail.userData.travel = true;
    s.group.add(tail);
    this.threads.push(tail);
  }

  /** The pile at the alcove, slips drifting down onto it, and the one that lifts to you. */
  private dressMemory(): void {
    const s = this.station("memory");
    const cliff = s.plates[2];
    const size = (cliff.geometry as THREE.PlaneGeometry).parameters.height * 0.06;
    for (let i = 0; i < 22; i++) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(size * 1.6, size * 0.45), new THREE.MeshBasicMaterial({ map: this.maps.slip, transparent: true, depthWrite: false, color: new THREE.Color(0.82, 0.85, 0.92), opacity: 0 }));
      const x = (Math.sin(i * 7.31) * 0.5 + 0.5) * 1.1 - 0.05;
      m.position.copy(this.stage.at(cliff, x, 0, 2 + (i % 5) * 0.6));
      s.group.add(m);
      this.falling.push({ mesh: m, x, speed: 0.6 + (i % 7) * 0.08, phase: i * 0.37 });
    }
    this.lifted = new THREE.Mesh(new THREE.PlaneGeometry(size * 3.2, size * 0.9), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, "Alex: someone should sort this before Friday"), transparent: true, depthWrite: false, color: new THREE.Color(0.86, 0.88, 0.94), opacity: 0 }));
    this.liftFrom.copy(this.stage.at(cliff, 0.79, 0.75, 1.5));
    this.lifted.position.copy(this.liftFrom);
    s.group.add(this.lifted);
  }

  /** The constellation: slips rising off the ridge and locking into a still map. */
  private dressRidge(): void {
    const s = this.station("ridge");
    const words = this.words;
    const N = 26;
    const rnd = (i: number, k: number) => Math.sin(i * 91.7 + k * 13.3) * 0.5 + 0.5;
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i < N; i++) {
      const text = words[i % Math.max(1, words.length)] ?? "";
      const w = 0.55 + rnd(i, 4) * 0.35;
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, w * 0.28), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, i < words.length ? text : ""), transparent: true, depthWrite: false, color: new THREE.Color(0.85, 0.88, 0.97), opacity: 0 }));
      const to = new THREE.Vector3((rnd(i, 1) - 0.5) * 12, -1.1 + rnd(i, 2) * 4.2, -5 - rnd(i, 3) * 8);
      const from = new THREE.Vector3(to.x * 0.6, -3.2 - rnd(i, 5), -4 - rnd(i, 6) * 3);
      m.position.copy(from);
      m.rotation.z = (rnd(i, 7) - 0.5) * 0.3;
      s.group.add(m);
      this.stars.push({ mesh: m, from, to });
      pts.push(to);
    }
    const seg: number[] = [];
    pts.forEach((p, i) => {
      const near = pts.map((q, j) => [p.distanceTo(q), j] as [number, number]).filter(([, j]) => j !== i).sort((a, b) => a[0] - b[0]).slice(0, 2);
      for (const [, j] of near) if (j > i) seg.push(p.x, p.y, p.z, pts[j].x, pts[j].y, pts[j].z);
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(seg, 3));
    this.starLinks = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: PALE, transparent: true, opacity: 0 }));
    g.setDrawRange(0, 0);
    s.group.add(this.starLinks);
  }

  /** A warm light at each pavilion, and a lantern for when the circles are shown apart. */
  private dressCircles(): void {
    const s = this.station("circles");
    const land = s.plates[2];
    for (const key of ["harbour", "slope", "terraces", "spur"] as const) {
      const [u, v] = meta.pavilions[key];
      const light = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.6), new THREE.MeshBasicMaterial({ map: radial("255,196,140"), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0 }));
      light.position.copy(this.stage.at(land, u, v - 0.02, 0.05));
      s.group.add(light);
      this.pavilionLights.push(light);
      const l = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.5), new THREE.MeshBasicMaterial({ map: this.maps.lit, transparent: true, depthWrite: false, opacity: 0 }));
      l.position.copy(this.stage.at(land, u + 0.025, v - 0.05, 0.06));
      s.group.add(l);
      this.pavilionLanterns.push(l);
    }
  }
}
