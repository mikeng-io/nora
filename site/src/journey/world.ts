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

/** A word as a small paper chip with a node dot, for the knowledge map. */
function chip(text: string, hub: boolean): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  const g = c.getContext("2d")!;
  const font = `${hub ? 600 : 500} 44px "Inter", sans-serif`;
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 96;
  c.width = w;
  c.height = 84;
  g.font = font;
  g.fillStyle = hub ? "rgba(246,242,234,0.96)" : "rgba(232,237,248,0.9)";
  g.beginPath();
  g.roundRect(2, 2, w - 4, 80, 40);
  g.fill();
  g.fillStyle = "#6a7fb4";
  g.beginPath();
  g.arc(40, 42, 9, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#1a2134";
  g.fillText(text, 62, 57);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

/** A fine filament that sags a little between two points, drawn additively so it glows rather than sits. */
function thread(from: THREE.Vector3, to: THREE.Vector3, lift: number, width: number): THREE.Mesh {
  const mid = from.clone().lerp(to, 0.5).add(new THREE.Vector3(0, lift, 0.2));
  return new THREE.Mesh(
    new THREE.TubeGeometry(new THREE.CatmullRomCurve3([from, mid, to]), 120, width, 5, false),
    new THREE.MeshBasicMaterial({ color: PALE.clone(), transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
}

export class Lantern {
  readonly group = new THREE.Group();
  /** Where the paper body sits in the sprite (which also holds the bracket), from the sprite's centre. */
  readonly body: THREE.Vector3;
  private readonly lit: THREE.Mesh;
  private readonly spill: THREE.Mesh;
  constructor(unlit: THREE.Texture | null, litMap: THREE.Texture | null, size: number) {
    this.body = new THREE.Vector3(-0.13 * size, -0.11 * size, 0);
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
  links: THREE.Mesh[] = [];
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
    /** The slips' words, in the page's language: three path slips, the lifted slip, the night traveller. */
    private readonly slips: { path: string[]; lifted: string; traveller: string },
  ) {}

  /** A station's draw order: its stack, with the foreground (and on the path its night twin after it) last. */
  order(name: string): void {
    const s = this.station(name);
    const fore = s.plates[s.plates.length - 1];
    const last = name === "path" && this.night.length ? [fore, this.night[this.night.length - 1]] : [fore];
    this.stage.order(s.group, 0, last);
    last.forEach((o, i) => o.traverse((c) => (c.renderOrder = 900 + i)));
  }

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
    if (Object.values(this.maps).some((m) => !m)) throw new Error("props: a painting failed to load");
    await Promise.all(["circles", "memory", "ridge", "path", "terrace", "gate"].map((n) => this.build(n)));
    await this.dressPath();
    this.dressMemory();
    this.dressRidge();
    this.dressCircles();
    for (const name of this.stations.keys()) this.order(name);
  }

  private async build(name: string): Promise<void> {
    const s = await this.stage.station(name, LAYERS[name].map(([f, z, o]) => [art(`${name}/${f}`), z, o] as [string, number, number?]));
    if (s.plates.some((p) => !p.visible)) throw new Error(`station ${name}: a painting failed to load`);
    this.stage.fog(s.group, -22, name === "memory" || name === "ridge" ? 0.42 : 0.3, name === "hero" ? "#f1d9cc" : "#aebcd8", 0.55);
    this.stations.set(name, s);
  }

  /** The path at blue hour, its night twin laid over it, the lanterns, the slips and their threads. */
  private async dressPath(): Promise<void> {
    const s = this.station("path");
    const nightFiles = LAYERS.path.map(([f]) => f);
    const maps = await Promise.all(nightFiles.map((f) => this.stage.texture(art(`night/${f}`))));
    if (maps.some((m) => !m)) throw new Error("night: a painting failed to load");
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
    const lines = this.slips.path;
    const at: [number, number][] = [[0.67, 0.22], [0.6, 0.31], [0.71, 0.4]];
    lines.forEach((text, i) => {
      const p = this.stage.at(pp, at[i][0], at[i][1], 0.05 + i * 0.03);
      const m = new THREE.Mesh(new THREE.PlaneGeometry(LS * 1.5, LS * 0.42), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, text), transparent: true, depthWrite: false, color: new THREE.Color(0.8, 0.82, 0.9) }));
      m.position.copy(p);
      m.rotation.z = (i - 1) * 0.05;
      s.group.add(m);
      const eyelet = p.clone().add(new THREE.Vector3(-LS * 0.62, 0, 0));
      const body = this.lantern.group.position.clone().add(this.lantern.body).add(new THREE.Vector3(0, LS * 0.12, 0));
      const t = thread(eyelet, body, -LS * (0.18 + i * 0.08), LS * 0.0045);
      s.group.add(t);
      this.threads.push(t);
    });
    // the slip that comes in the night, from another conversation
    this.traveller = new THREE.Mesh(new THREE.PlaneGeometry(LS * 1.5, LS * 0.42), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, this.slips.traveller), transparent: true, depthWrite: false, opacity: 0 }));
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
    this.lifted = new THREE.Mesh(new THREE.PlaneGeometry(size * 2.6, size * 0.73), new THREE.MeshBasicMaterial({ map: slip(this.maps.slip, this.slips.lifted), transparent: true, depthWrite: false, color: new THREE.Color(0.86, 0.88, 0.94), opacity: 0 }));
    this.liftFrom.copy(this.stage.at(cliff, 0.79, 0.75, 1.5));
    this.lifted.position.copy(this.liftFrom);
    s.group.add(this.lifted);
  }

  /** Take two's map: the words of the conversation as nodes, joined by what
      actually relates them. It forms, complete, and then nothing moves. */
  private dressRidge(): void {
    const s = this.station("ridge");
    // nodes in the order of knowledge.nodes: Alex, Friday, the booking, Sam, Thursday, Mia, the table, someone
    const at: [number, number][] = [[-2.6, 0.9], [2.4, 1.1], [0, 0], [-1.9, -1.5], [2.2, -1.1], [3.6, 0.1], [0.6, -1.9], [-3.2, -0.5]];
    const edges: [number, number][] = [[0, 2], [7, 2], [0, 7], [2, 1], [2, 6], [3, 4], [5, 4], [4, 2]];
    const Z = -6;
    const pts = at.map(([x, y]) => new THREE.Vector3(x, y, Z));
    for (const [a, b] of edges) {
      const mid = pts[a].clone().lerp(pts[b], 0.5).add(new THREE.Vector3(0, 0.25, -0.2));
      const tube = new THREE.Mesh(
        new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(pts[a], mid, pts[b]), 48, 0.008, 5, false),
        new THREE.MeshBasicMaterial({ color: PALE, transparent: true, opacity: 0 }),
      );
      tube.geometry.setDrawRange(0, 0);
      s.group.add(tube);
      this.links.push(tube);
    }
    this.words.slice(0, at.length).forEach((word, i) => {
      const tex = chip(word, i === 2);
      const h = i === 2 ? 0.46 : 0.36;
      const m = new THREE.Mesh(new THREE.PlaneGeometry(h * (tex.image.width / tex.image.height), h), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, opacity: 0 }));
      const to = pts[i];
      const from = new THREE.Vector3(to.x * 0.4, to.y - 3.4, Z + 1.2);
      m.position.copy(from);
      s.group.add(m);
      this.stars.push({ mesh: m, from, to });
    });
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
