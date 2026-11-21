// What happens in each scene: where the camera goes, which station shows,
// what Nora does and how the light falls. One function per scene, each
// pure in its local progress l (0..1), so scrolling back plays it backwards.
import * as THREE from "three";
import type { Stage } from "./stage";
import type { World } from "./world";
import { COPPER, PALE } from "./world";
import type { Nora } from "./nora";
import { ease, lerp, ramp, type SceneId } from "./timeline";
import meta from "../assets/journey/meta.json";

type V3 = [number, number, number];
interface Shot {
  from: V3;
  to: V3;
  lookFrom: [number, number];
  lookTo: [number, number];
}

const SHOTS: Record<string, Shot> = {
  hero: { from: [-0.2, 1.5, 10.6], to: [0.45, -0.15, 8.4], lookFrom: [-0.3, 0.9], lookTo: [0.6, -0.45] },
  circles: { from: [-1.3, 1.4, 10.4], to: [1.2, 0.5, 9.0], lookFrom: [-0.9, -0.2], lookTo: [0.9, -0.6] },
  memory: { from: [0.5, 0.35, 10.4], to: [1.35, -0.25, 7.1], lookFrom: [0.3, 0.2], lookTo: [1.9, -0.6] },
  knowledge: { from: [0, -0.4, 10.4], to: [0.2, 0.7, 9.2], lookFrom: [0, -0.4], lookTo: [0.2, 0.9] },
  nora: { from: [0, 0.25, 10.4], to: [0.55, -0.1, 8.3], lookFrom: [0, 0], lookTo: [0.45, -0.3] },
  shift: { from: [0.55, -0.1, 8.3], to: [0.15, 0.25, 9.6], lookFrom: [0.45, -0.3], lookTo: [0.2, 0.6] },
  act: { from: [-0.3, 0.15, 10.2], to: [0.25, -0.05, 9.1], lookFrom: [-0.2, 0], lookTo: [0.3, -0.15] },
  rooms: { from: [0.8, 3.2, 9.6], to: [-0.6, 2.4, 8.6], lookFrom: [0.4, -1.3], lookTo: [-0.4, -1.0] },
  soulbound: { from: [0.3, 0.05, 9.4], to: [0.4, -0.05, 8.9], lookFrom: [0.3, -0.1], lookTo: [0.4, -0.2] },
};

export interface Frame {
  /** Lantern lit (0..1), held by the visitor in the Nora scene. */
  lit: number;
  /** The soulbound split, 0..1 across the screen. */
  split: number;
}

export class Director {
  private readonly white = new THREE.Color(1, 1, 1);
  private readonly cool = new THREE.Color(0.8, 0.85, 1);
  private readonly warm = new THREE.Color(1.04, 0.93, 0.82);
  private readonly nightTint = new THREE.Color(0.62, 0.68, 0.86);
  private readonly tmp = new THREE.Color();

  constructor(
    private readonly stage: Stage,
    private readonly world: World,
    private readonly nora: Nora,
  ) {}

  show(names: string[]): void {
    for (const [n, s] of this.world.stations) s.group.visible = names.includes(n);
  }

  shot(name: string, l: number, push = 0): void {
    const s = SHOTS[name];
    const e = ease(l);
    const c = this.stage.camera;
    c.position.set(lerp(s.from[0], s.to[0], e), lerp(s.from[1], s.to[1], e), lerp(s.from[2], s.to[2], e) - push);
    c.lookAt(lerp(s.lookFrom[0], s.lookTo[0], e), lerp(s.lookFrom[1], s.lookTo[1], e), -20);
  }

  /** Reset the light every frame; each scene adds what it needs. */
  private grade(cool: number, warm: number, night: number): void {
    const u = this.stage.grade.uniforms;
    u.uCool.value = cool;
    u.uWarm.value = warm;
    u.uNight.value = night;
    u.uRewind.value = 0;
    u.uSplit.value = -1;
    u.uFlash.value = 0;
    this.stage.bloom.strength = 0;
  }

  private stand(station: string, hop = true): void {
    const spot = (meta.spots as Record<string, { layer: number; feet: number[]; height: number }>)[station];
    const s = this.world.station(station);
    const plate = s.plates[spot.layer];
    const h = (plate.geometry as THREE.PlaneGeometry).parameters.height * spot.height;
    this.nora.place(s.group, this.stage.at(plate, spot.feet[0], spot.feet[1], 0.03), h, hop);
    this.world.order(station);
  }

  run(id: SceneId, l: number, p: number, f: Frame, t: number): void {
    switch (id) {
      case "hero": return this.hero(l);
      case "circles": return this.circles(l, false);
      case "memory": return this.memory(l, t);
      case "knowledge": return this.knowledge(l);
      case "rewind": return this.rewind(l);
      case "nora": return this.noraScene(l, f, t);
      case "shift": return this.shift(l, t);
      case "act": return this.act(l);
      case "rooms": return this.circles(l, true);
      case "soulbound": return this.soulbound(l, f, t);
      case "gate": return this.gate(l, p);
    }
  }

  private hero(l: number): void {
    this.show(["hero"]);
    this.shot("hero", l);
    this.grade(0.15, 0.5, 0);
    this.stand("hero");
    this.nora.light(1, this.tmp.setRGB(1, 0.97, 0.94), 0.35);   // she is there from the first frame
  }

  private circles(l: number, apart: boolean): void {
    this.show(["circles"]);
    this.shot(apart ? "rooms" : "circles", l);
    this.grade(apart ? 0.6 : 0.1, apart ? 0.15 : 0.55, apart ? 0.45 : 0);
    this.nora.leave();
    this.world.pavilionLights.forEach((m, i) => {
      const k = apart ? ramp(l, 0.1, 0.3) : ease(ramp(l, 0.2 + i * 0.12, 0.32 + i * 0.12));
      (m.material as THREE.MeshBasicMaterial).opacity = 0.7 * k;
    });
    this.world.pavilionLanterns.forEach((m, i) => {
      (m.material as THREE.MeshBasicMaterial).opacity = apart ? ease(ramp(l, 0.15 + i * 0.08, 0.3 + i * 0.08)) : 0;
    });
  }

  private memory(l: number, t: number): void {
    this.show(["memory"]);
    this.shot("memory", l);
    this.grade(1, 0, 0);
    this.nora.leave();
    const H = this.world.station("memory").plates[2];
    const top = this.stage.at(H, 0, 0).y, bottom = this.stage.at(H, 0, 0.9).y;
    for (const f of this.world.falling) {
      const k = (t * 0.05 * f.speed + f.phase) % 1;
      f.mesh.position.y = lerp(top, bottom, k);
      f.mesh.position.x = this.stage.at(H, f.x + Math.sin(t * 0.7 + f.phase * 9) * 0.02, 0).x;
      f.mesh.rotation.z = Math.sin(t * 0.9 + f.phase * 5) * 0.6;
      (f.mesh.material as THREE.MeshBasicMaterial).opacity = 0.85 * ramp(l, 0.05, 0.2) * (1 - ramp(k, 0.85, 1));
    }
    // asked, one slip lifts to the lens, then drops back: the moment passed anyway
    const lift = ease(ramp(l, 0.35, 0.55)) * (1 - ease(ramp(l, 0.7, 0.88)));
    const cam = this.stage.camera.position;
    const m = this.world.lifted;
    m.position.copy(this.world.liftFrom).lerp(new THREE.Vector3(cam.x + 2.1, cam.y + 0.55, cam.z - 7.5), lift);
    m.position.y -= ease(ramp(l, 0.7, 0.88)) * 1.2;
    m.rotation.z = (1 - lift) * 0.4;
    (m.material as THREE.MeshBasicMaterial).opacity = ramp(l, 0.3, 0.38) * (1 - ramp(l, 0.86, 0.94));
  }

  private knowledge(l: number): void {
    this.show(["ridge"]);
    // the camera settles on the map as it forms, and stops when it is whole
    this.shot("knowledge", Math.min(l, 0.7) / 0.7);
    this.grade(0.9, 0, 0.2);
    this.nora.leave();
    const fade = 1 - ramp(l, 0.94, 1);
    this.world.stars.forEach(({ mesh, from, to }, i) => {
      const k = ease(ramp(l, 0.06 + i * 0.03, 0.3 + i * 0.03));
      mesh.position.lerpVectors(from, to, k);
      (mesh.material as THREE.MeshBasicMaterial).opacity = ramp(l, 0.04 + i * 0.03, 0.14 + i * 0.03) * fade;
    });
    this.world.links.forEach((tube, i) => {
      const k = ease(ramp(l, 0.36 + i * 0.03, 0.5 + i * 0.03));
      const g = tube.geometry;
      g.setDrawRange(0, Math.floor((g.index?.count ?? 0) * k));
      (tube.material as THREE.MeshBasicMaterial).opacity = 0.8 * Math.min(1, k * 3) * fade;
    });
  }

  /** The take runs backwards: ridge, alcove, pavilions, harbour, as tape. */
  private rewind(l: number): void {
    const order = ["knowledge", "memory", "circles", "hero"] as const;
    const stations = ["ridge", "memory", "circles", "hero"];
    const i = Math.min(3, Math.floor(l * 4));
    const local = 1 - (l * 4 - i);
    this.show([stations[i]]);
    this.shot(order[i], local * 0.9);
    this.grade(0.4, 0, 0);
    this.nora.leave();
    this.stage.grade.uniforms.uRewind.value = Math.min(1, ramp(l, 0, 0.08) * 1.2) * (1 - ramp(l, 0.9, 1));
    this.stage.grade.uniforms.uFlash.value = ease(ramp(l, 0.88, 1));
  }

  private noraScene(l: number, f: Frame, t: number): void {
    this.show(["path"]);
    this.shot("nora", l);
    this.grade(1, f.lit, 0);
    this.stage.grade.uniforms.uFlash.value = 1 - ease(ramp(l, 0, 0.1));
    this.stand("path", l > 0.06);
    this.world.night.forEach((m) => ((m.material as THREE.MeshBasicMaterial).opacity = 0));
    this.world.rail.forEach((r) => (r.group.visible = false));
    this.world.traveller.visible = false;
    this.world.lantern.set(f.lit, t);
    this.stage.bloom.strength = 0.5 * f.lit;
    this.tmp.copy(this.cool).lerp(this.warm, f.lit * 0.6);
    this.nora.light(ramp(l, 0.06, 0.1), this.tmp, 0.9 * f.lit);
    this.threads(f.lit, false);
  }

  /** Night falls on the path; a slip comes in from another conversation. */
  private shift(l: number, t: number): void {
    this.show(["path"]);
    this.shot("shift", l);
    const night = ease(ramp(l, 0, 0.3));
    this.grade(1, 1 - night * 0.6, night);
    this.stand("path", false);
    this.world.night.forEach((m) => ((m.material as THREE.MeshBasicMaterial).opacity = night));
    this.world.lantern.set(1, t);
    this.stage.bloom.strength = 0.55;
    this.tmp.copy(this.warm).lerp(this.nightTint, night * 0.7);
    this.nora.light(1, this.tmp, 0.9);
    const k = ease(ramp(l, 0.32, 0.7));
    const tr = this.world.traveller;
    tr.visible = true;
    tr.position.copy(this.world.travelPath.getPointAt(k));
    tr.rotation.z = Math.sin(t * 1.3) * 0.05 * (1 - k);
    (tr.material as THREE.MeshBasicMaterial).opacity = ramp(l, 0.28, 0.36);
    this.threads(1, true, ramp(l, 0.3, 0.72));
    if (l > 0.3 && l < 0.75) this.nora.player.lookAt(0.8, 0.4);
    else this.nora.player.lookAt(null);
  }

  private act(l: number): void {
    this.show(["terrace"]);
    this.shot("act", l);
    this.grade(0.1, 0.4, 0);
    this.stand("terrace");
    this.nora.light(ramp(l, 0.04, 0.1), this.tmp.setRGB(1, 0.98, 0.95), 0.2);
  }

  /** Two takes of one night at once: without her on the left, with her on the right. */
  private soulbound(l: number, f: Frame, t: number): void {
    this.show(["path"]);
    this.shot("soulbound", l);
    this.grade(1, 0.5, 1);
    this.stand("path", false);
    this.world.night.forEach((m) => ((m.material as THREE.MeshBasicMaterial).opacity = 1));
    this.world.traveller.visible = false;
    this.world.rail.forEach((r) => (r.group.visible = true));
    this.stage.grade.uniforms.uSplit.value = f.split;
    this.stage.bloom.strength = 0.6;
    this.tmp.copy(this.warm).lerp(this.nightTint, 0.5);
    this.nora.light(1, this.tmp, 0.9);
    this.world.lantern.set(1, t);
    this.world.rail.forEach((r, i) => r.set(ease(ramp(l, 0.05 + i * 0.05, 0.2 + i * 0.05)), t + i));
    this.threads(1, false);
  }

  /** The other half of the soulbound frame: the same night with nothing burning and nobody there. */
  without(t: number): void {
    this.world.lantern.set(0, t);
    this.world.rail.forEach((r) => r.set(0, t));
    this.nora.light(0, this.white, 0);
    this.threads(0, false);
    this.stage.bloom.strength = 0;
  }

  private gate(l: number, p: number): void {
    this.show(["gate"]);
    const s = this.world.station("gate");
    const wall = s.plates[2];
    const c = this.stage.at(wall, meta.gate.center[0], meta.gate.center[1]);
    const fly = ease(ramp(l, 0.45, 1));
    const cam = this.stage.camera;
    cam.position.set(lerp(0, c.x, fly), lerp(-0.3, c.y, fly), lerp(10.4, wall.position.z - 1.5, Math.pow(fly, 1.6)));
    cam.lookAt(lerp(0, c.x, fly), lerp(0.1, c.y, fly), -40);
    this.grade(0.1, 0.6, 0);
    this.stage.grade.uniforms.uFlash.value = ease(ramp(p, 0.975, 1));
    if (l < 0.5) {
      this.stand("gate");
      this.nora.light(ramp(l, 0.04, 0.1) * (1 - ramp(l, 0.4, 0.5)), this.tmp.setRGB(1, 0.97, 0.93), 0.3);
    } else this.nora.leave();
  }

  private threads(k: number, withTraveller: boolean, travel = 0): void {
    for (const tube of this.world.threads) {
      const m = tube.material as THREE.MeshBasicMaterial;
      if (tube.userData.travel) {
        tube.visible = withTraveller;
        m.color.copy(COPPER);
        m.opacity = 0.75 * travel;
        continue;
      }
      m.color.copy(PALE).lerp(COPPER, ease(k));
      m.opacity = 0.3 + 0.6 * ease(k);
    }
  }
}
