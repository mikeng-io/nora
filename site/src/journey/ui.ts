// The parts of the stage you touch, all real HTML over the world: the hold
// that lights her lantern, the labels that ride on painted things, the
// soulbound split you drag, the rewind's counter, the scene rail and cursor.
// None is required to read on (DESIGN.md §6): every one has a scroll fallback.
import * as THREE from "three";

const $ = <T extends HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector<T>(sel);

export class Ui {
  lit = 0;
  split = 0.5;
  private holding = false;
  private dragX = -1;
  private dragging = false;
  private done = false;
  private readonly hold = $<HTMLButtonElement>("[data-hold]");
  private readonly cursor = $<HTMLElement>("[data-cursor]");
  private readonly tag = $<HTMLElement>("[data-tag]");
  private readonly code = $<HTMLElement>("[data-timecode]");
  private readonly splitEl = $<HTMLElement>("[data-split-ui]");
  private readonly labels = [...document.querySelectorAll<HTMLElement>("[data-pavilion]")];
  private readonly rail = [...document.querySelectorAll<HTMLAnchorElement>("[data-rail] a")];
  onLit: () => void = () => {};

  constructor() {
    const fine = matchMedia("(pointer: fine)").matches;
    if (this.cursor && fine) document.documentElement.classList.add("own-cursor");
    addEventListener("pointermove", (e) => {
      if (this.cursor) this.cursor.style.transform = `translate3d(${e.clientX}px, ${e.clientY}px, 0)`;
      if (this.dragging) this.dragX = e.clientX / innerWidth;
    });
    // the soulbound split is dragged: press, move, release
    addEventListener("pointerdown", (e) => {
      if (!this.splitEl?.classList.contains("on")) return;
      this.dragging = true;
      this.dragX = e.clientX / innerWidth;
    });
    for (const ev of ["pointerup", "pointercancel", "blur"]) addEventListener(ev, () => (this.dragging = false));
    if (this.hold) {
      const on = (e: Event) => {
        e.preventDefault();
        this.holding = true;
        this.cursor?.classList.add("big");
      };
      const off = () => {
        this.holding = false;
      };
      this.hold.addEventListener("pointerdown", on);
      this.hold.addEventListener("keydown", (e) => (e.key === " " || e.key === "Enter") && on(e));
      this.hold.addEventListener("keyup", off);
      addEventListener("pointerup", off);
      this.hold.addEventListener("pointerleave", () => this.cursor?.classList.remove("big"));
      this.hold.addEventListener("pointerenter", () => this.cursor?.classList.add("big"));
    }
  }

  /** The lantern: held by the visitor, or lit by scrolling on past it. */
  holdFrame(active: boolean, l: number, at: THREE.Vector3 | null, dt: number): void {
    const ready = active && l > 0.2 && l < 0.82 && this.lit < 1;
    if (this.hold) {
      this.hold.hidden = !ready;
      if (ready && at) this.place(this.hold, at);
    }
    if (!active) return;
    if (this.holding && this.lit < 1) this.lit = Math.min(1, this.lit + dt / 1.3);
    else if (this.lit < 1) this.lit = Math.max(0, this.lit - dt / 2);
    if (l > 0.82) this.lit = Math.max(this.lit, Math.min(1, (l - 0.82) / 0.08));
    if (l < 0.15) {
      this.lit = 0;
      this.done = false;
    }
    if (this.lit >= 1 && !this.done) {
      this.done = true;
      this.onLit();
    }
    this.hold?.style.setProperty("--k", this.lit.toFixed(3));
  }

  /** The words on her lantern's tag, riding beside it. */
  tagFrame(visible: boolean, at: THREE.Vector3 | null, moved: number): void {
    if (!this.tag) return;
    this.tag.classList.toggle("on", visible);
    if (visible && at) this.place(this.tag, at);
    this.tag.classList.toggle("moved", moved > 0.5);
  }

  labelFrame(points: (THREE.Vector3 | null)[], k: number[]): void {
    this.labels.forEach((el, i) => {
      const at = points[i];
      el.classList.toggle("on", (k[i] ?? 0) > 0.01 && !!at);
      el.style.opacity = String(k[i] ?? 0);
      if (at) this.place(el, at);
    });
  }

  /** The soulbound split: follows the pointer once it moves, otherwise scroll sweeps it. */
  splitFrame(active: boolean, l: number): number {
    if (this.splitEl) this.splitEl.classList.toggle("on", active);
    const auto = 0.78 - 0.3 * Math.min(1, l * 1.6);
    if (!active) this.dragX = -1;
    const want = this.dragX >= 0 && active ? Math.min(0.92, Math.max(0.08, this.dragX)) : auto;
    this.split += (want - this.split) * 0.12;
    this.splitEl?.style.setProperty("--x", `${(this.split * 100).toFixed(2)}%`);
    return this.split;
  }

  timecode(active: boolean, l: number): void {
    if (!this.code) return;
    this.code.classList.toggle("on", active);
    if (!active) return;
    const s = Math.max(0, Math.round((1 - l) * 72));
    this.code.dataset.t = `◀◀ 00:${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  railFrame(index: number): void {
    this.rail.forEach((a, i) => a.toggleAttribute("aria-current", i === index));
  }

  /** Put an element at a world point, in screen pixels. */
  project = (camera: THREE.Camera) => (v: THREE.Vector3): THREE.Vector3 => v.clone().project(camera);

  private place(el: HTMLElement, ndc: THREE.Vector3): void {
    el.style.left = `${((ndc.x * 0.5 + 0.5) * innerWidth).toFixed(1)}px`;
    el.style.top = `${((-ndc.y * 0.5 + 0.5) * innerHeight).toFixed(1)}px`;
  }
}
