// The stage, progressively: without script, with reduced motion, without
// WebGL2 or if anything here fails, the page stays what it is in the HTML, the
// whole story as stills and words (DESIGN.md §6). Otherwise html.stage-on turns
// the same words into the scroll-told journey over the painted world.
import * as THREE from "three";
import Lenis from "lenis";
import { Player } from "@livliv-dev/player";
import noraUrl from "../assets/journey/nora.livliv?url";
import meta from "../assets/journey/meta.json";
import { Stage } from "./stage";
import { World } from "./world";
import { Nora } from "./nora";
import { Veil } from "./veil";
import { Type } from "./type";
import { Ui } from "./ui";
import { Chat } from "./chat";
import { Director } from "./director";
import { SCENES, bank, ease, ramp, sceneAt } from "./timeline";

const root = document.documentElement;
const canvas = document.querySelector<HTMLCanvasElement>("[data-stage]");
const noraCanvas = document.querySelector<HTMLCanvasElement>("[data-nora-canvas]");
const story = document.querySelector<HTMLElement>("[data-story]");

const supported = () => {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  if ((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData) return false;
  return Player.isSupported() && !!document.createElement("canvas").getContext("webgl2");
};

if (canvas && noraCanvas && story && supported()) {
  start(canvas, noraCanvas, story).catch((e) => console.error(e));
}

async function start(canvas: HTMLCanvasElement, noraCanvas: HTMLCanvasElement, story: HTMLElement): Promise<void> {
  // Everything the stage makes is registered here as it is made; any failure,
  // at startup or later, runs the same release in reverse and leaves the page
  // as written: the whole story as stills and words.
  const undo: (() => void)[] = [];
  let released = false;
  const release = () => {
    released = true;
    while (undo.length) {
      try {
        undo.pop()!();
      } catch (e) {
        console.error(e);
      }
    }
    root.classList.remove("stage-on", "stage-loading", "stage-waiting", "own-cursor");
  };
  root.classList.add("stage-on", "stage-loading");
  scrollTo(0, 0);
  const stage = new Stage(canvas);
  undo.push(() => stage.dispose());
  const words = (story.dataset.nodes ?? "").split("|").filter(Boolean);
  const slips = JSON.parse(story.dataset.slips ?? "{}") as { path: string[]; lifted: string; traveller: string };
  const world = new World(stage, words, slips);
  let nora: Nora;
  try {
    const noraLoad = Nora.create(noraCanvas, noraUrl);
    noraLoad.then((n) => undo.push(() => n.player.dispose({ release: true })), () => {});
    [nora] = await Promise.all([noraLoad, world.loadHero()]);
  } catch (e) {
    release();
    throw e;
  }
  const veil = new Veil(stage.camera, world.transit);
  const type = new Type(story);
  undo.push(() => type.restore());
  const rt = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType });
  undo.push(() => rt.dispose());
  const life = new AbortController();   // every stage listener ends with the stage
  undo.push(() => life.abort());
  const ui = new Ui(life.signal);
  const chat = new Chat(life.signal);
  undo.push(() => chat.detach());
  const director = new Director(stage, world, nora);
  let rest = false;
  world.loadRest().then(
    () => (rest = true),
    (e) => {
      console.error(e);
      release();
    },
  );

  ui.onLit = () => {
    nora.player.act("nod");
    nora.player.setExpression("smile");
    setTimeout(() => nora.player.setExpression(null), 2400);
  };
  chat.onEvent = (kind) => {
    if (kind === "ask") nora.player.lookAt(-0.9, 0.05);
    if (kind === "reply" || kind === "own") {
      nora.player.act("nod");
      setTimeout(() => nora.player.lookAt(null), 1800);
    }
  };

  // the chat's log scrolls itself (data-lenis-prevent on it)
  const lenis = new Lenis({ lerp: 0.08, wheelMultiplier: 0.85 });
  undo.push(() => lenis.destroy());
  const max = () => document.documentElement.scrollHeight - innerHeight;
  for (const a of document.querySelectorAll<HTMLAnchorElement>("a[data-scene]")) {
    a.addEventListener("click", (e) => {
      const s = SCENES.find((x) => x.id === a.dataset.scene);
      if (!s) return;
      e.preventDefault();
      lenis.scrollTo(Math.ceil((s.a + 0.006) * max()), { duration: 2.2 });
    }, { signal: life.signal });
  }
  addEventListener("resize", () => stage.resize(), { signal: life.signal });

  const land = world.station("hero");
  let opened = 0;
  let last = performance.now();
  const t0 = last;

  const frame = (now: number) => {
    if (released) return;
    lenis.raf(now);
    const t = (now - t0) / 1000, dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    const p = Math.min(1, Math.max(0, scrollY / Math.max(1, max())));
    const { scene, l, index } = sceneAt(p);
    // ahead of the paintings: show this scene's still and words until its station arrives
    const waiting = !rest && scene.station !== "hero";
    root.classList.toggle("stage-waiting", waiting);
    // the page opens inside the cloud and leaves it on its own, then scroll takes over
    if (!opened) opened = now;
    const intro = Math.min(1, (now - opened) / 2400);
    const b = bank(p);
    veil.set(intro < 1 ? 0.5 + 0.5 * intro : b.k, intro < 1 ? true : b.warm);

    const f = { lit: ui.lit, split: ui.split };
    if (!waiting) director.run(scene.id, l, p, f, t);
    if (rest) overlays(scene.id, l, dt);
    if (scene.id === "soulbound" && rest) {
      // the left of the frame is the same night without her: render it, then restore
      rt.setSize(innerWidth, innerHeight);
      director.without(t);
      stage.renderer.setRenderTarget(rt);
      stage.renderer.render(stage.scene, stage.camera);
      stage.renderer.setRenderTarget(null);
      if (!waiting) director.run(scene.id, l, p, f, t);
      stage.grade.uniforms.tAlt.value = rt.texture;
    }
    const lt = waiting ? 0.5 : scene.id === "hero" ? Math.max(l, 0.26 * ease(ramp(intro, 0.35, 1))) : scene.id === "gate" ? Math.min(l, 0.75) : l;
    chat.live(!waiting);
    type.show(scene.id, lt);
    type.hideAll(new Set([scene.id]));
    ui.railFrame(index);
    stage.render(t);
    if (root.classList.contains("stage-loading") && land) root.classList.remove("stage-loading");
    requestAnimationFrame(frame);
  };

  /** The HTML that rides on the world: the hold, the tag, the labels, the split, the counter, the chat. */
  const overlays = (id: string, l: number, dt: number) => {
    const project = ui.project(stage.camera);
    const lantern = world.lantern.group.localToWorld(world.lantern.body.clone());
    ui.holdFrame(id === "nora", l, id === "nora" ? project(lantern) : null, dt);
    const tagOn = (id === "nora" && ui.lit > 0.9) || (id === "shift" && l > 0.1 && l < 0.9);
    ui.tagFrame(tagOn, tagOn ? project(lantern.clone().add(new THREE.Vector3(0.05, -0.55, 0))) : null, id === "shift" ? ramp(l, 0.66, 0.72) : 0);
    const circles = world.station("circles");
    const land3 = circles.plates[2];
    const keys = ["harbour", "slope", "terraces", "spur"] as const;
    const on = id === "circles" || id === "rooms";
    const pts = keys.map((k) => (on ? project(stage.at(land3, meta.pavilions[k][0], meta.pavilions[k][1] - 0.012).applyMatrix4(circles.group.matrixWorld)) : null));
    const ks = keys.map((_, i) => (id === "circles" ? ease(ramp(l, 0.2 + i * 0.12, 0.32 + i * 0.12)) : id === "rooms" ? ramp(l, 0.1, 0.3) : 0) * (1 - ramp(l, 0.9, 1)));
    ui.labelFrame(pts, ks);
    ui.splitFrame(id === "soulbound", l);
    ui.timecode(id === "rewind", l);
    chat.frame(id === "act", l);
  };
  requestAnimationFrame(frame);
}
