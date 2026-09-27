// Where she is. The page has several places she can be (the hero's studio,
// the edge of the chat window, the memory timeline, the manners column, the
// photo plate, the portal panel), each marked `data-nora-slot`, each with
// her static render in it for a page without script. With script she is in
// ONE of them at a time: the one you are reading. When you move on, she
// hops over, and the place she left stands empty (the hero's studio shows
// its empty sweep; the others simply have nobody in them).
//
// One canvas, one brain, one of her. Rendering runs only while she is on
// screen or in the air, and stops when the tab is hidden.
import { Brain, type Body, type Kind } from "./brain";
import { Renderer, type Rect } from "./rig";
import type { Frame, PoseId, PoseRig } from "./types";

export interface SlotInfo {
  id: string;
  el: HTMLElement;
  figure: HTMLElement;
  kind: Kind;
  pose: PoseId;
  poseDark?: PoseId;
  framed: boolean;
  host: HTMLElement;
}

interface Flight {
  from: SlotInfo | null;
  to: SlotInfo;
  start: number;
  dur: number;
  fromRect: Rect;
  dir: number;
  /** Coming in from beyond the edge of the screen, rather than from a place in view. */
  dropIn: boolean;
}

type Listener = (slot: string) => void;

const HOP: PoseId = "hop";

export class Stage {
  readonly brain = new Brain();
  readonly slots: SlotInfo[];
  private r: Renderer;
  private rigs: Record<string, PoseRig>;
  private urls: Record<string, string>;
  private current: SlotInfo | null = null;
  private flight: Flight | null = null;
  private candidate: { slot: SlotInfo; since: number } | null = null;
  private raf = 0;
  private last = 0;
  private onScreen = true;
  private flightBox: HTMLElement;
  private arriveListeners: Listener[] = [];
  private lastScroll = { y: scrollY, t: 0, v: 0 };
  private dark = false;
  private destroyed = false;
  private visibleSlots = new Set<SlotInfo>();
  private io: IntersectionObserver;
  private near: IntersectionObserver | null = null;
  /** Every listener the stage adds, removed together when it stops. */
  private listening = new AbortController();
  /** Textures that failed to load, and when: not asked for again every frame. */
  private failed = new Map<PoseId, { at: number; n: number }>();
  frame: Frame | null = null;

  constructor(rigs: Record<string, PoseRig>, urls: Record<string, string>, onFail: () => void) {
    this.rigs = rigs;
    this.urls = urls;
    this.r = new Renderer(onFail);
    this.slots = [...document.querySelectorAll<HTMLElement>("[data-nora-slot]")].map((el) => {
      const figure = el.querySelector<HTMLElement>("[data-nora-figure]") ?? el;
      const host = document.createElement("div");
      host.className = "nora-host";
      host.setAttribute("aria-hidden", "true");
      figure.append(host);
      return {
        id: el.dataset.noraSlot!,
        el,
        figure,
        kind: (el.dataset.kind ?? "stand") as Kind,
        pose: el.dataset.pose as PoseId,
        poseDark: (el.dataset.poseDark as PoseId) || undefined,
        framed: el.dataset.framed !== undefined,
        host,
      };
    });
    this.flightBox = document.createElement("div");
    this.flightBox.className = "nora-flight";
    this.flightBox.setAttribute("aria-hidden", "true");
    document.body.append(this.flightBox);
    this.io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const s = this.slots.find((x) => x.figure === e.target);
          if (!s) continue;
          if (e.isIntersecting) this.visibleSlots.add(s);
          else this.visibleSlots.delete(s);
        }
        this.onScreen = Boolean((this.current && this.visibleSlots.has(this.current)) || this.flight);
        this.wake();
      },
      { rootMargin: "120px 0px" },
    );
    for (const s of this.slots) this.io.observe(s.figure);
  }

  /** Calls `fn` whenever she arrives somewhere, and now, for where she already is. */
  onArrive(fn: Listener): void {
    this.arriveListeners.push(fn);
    if (this.current) fn(this.current.id);
  }

  get slot(): SlotInfo | null {
    return this.current;
  }

  setDark(dark: boolean, now: number): void {
    this.dark = dark;
    this.brain.setLight(dark, now);
    // the swap is inside a view transition: draw the new lighting now, so the
    // new snapshot already has it
    if (this.current?.poseDark) {
      void this.ensure(this.poseOf(this.current))
        .then(() => this.render(performance.now() / 1000))
        .catch(() => undefined);
    }
  }

  poseOf(s: SlotInfo): PoseId {
    return this.dark && s.poseDark ? s.poseDark : s.pose;
  }

  /** The pose to draw a slot in now: its lighting's, or the other one until that has loaded. */
  private drawable(s: SlotInfo): PoseId {
    const want = this.poseOf(s);
    if (this.r.has(want) || !s.poseDark) return want;
    return want === s.pose ? s.poseDark : s.pose;
  }

  /** Loads what a slot needs; resolves when it can be drawn. A texture that
      arrives after the scrolling stopped may be the one a place in view was
      waiting for, so its arrival is a reason to look again. */
  ensure(pose: PoseId): Promise<void> {
    const known = this.r.has(pose);
    // a texture that failed waits before it is tried again, longer each time
    const f = this.failed.get(pose);
    const t = performance.now() / 1000;
    if (!known && f && t - f.at < Math.min(60, 8 * 2 ** (f.n - 1))) return Promise.reject(new Error("backing off"));
    const job = this.r.load(pose, this.urls[pose], this.rigs[pose], () => this.quiet());
    if (!known) {
      void job
        .then(() => {
          this.failed.delete(pose);
          if (this.destroyed) return;
          this.consider(performance.now() / 1000);
          this.wake();
        })
        .catch(() => {
          const was = this.failed.get(pose);
          this.failed.set(pose, { at: performance.now() / 1000, n: (was?.n ?? 0) + 1 });
        });
    }
    return job;
  }

  /** Resolves at a quiet moment: no scroll for a little while, and the page idle. */
  private quiet(): Promise<void> {
    return new Promise((resolve) => {
      const idle = window.requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 60));
      const check = () => {
        if (performance.now() / 1000 - this.lastScroll.t < 0.25) setTimeout(check, 120);
        else idle(() => resolve(), { timeout: 800 });
      };
      check();
    });
  }

  async start(): Promise<void> {
    // the lighting before anything is loaded or drawn, so a late-night page
    // does not start her in daylight
    const root = document.documentElement;
    this.dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const first = this.pick(true);
    if (!first) throw new Error("no slot");
    await this.ensure(this.poseOf(first));
    // stopped while that loaded (reduced motion turned on, a lost context):
    // touch nothing, the static page is already back
    if (this.destroyed) throw new Error("stopped");
    this.place(first);
    for (const s of this.slots) if (s !== first) s.el.dataset.state = "away";
    this.bind();
    this.wake();
    // the hop now; every other place as it comes within a screen and a half
    void this.ensure(HOP).catch(() => undefined);
    const near = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const s = this.slots.find((x) => x.figure === e.target);
          if (!s) continue;
          // this lighting first; the other one after, so a switch is instant
          void this.ensure(this.poseOf(s))
            .then(() => s.poseDark && this.ensure(this.poseOf(s) === s.pose ? s.poseDark : s.pose))
            .catch(() => undefined);
        }
      },
      { rootMargin: "150% 0px" },
    );
    for (const s of this.slots) near.observe(s.figure);
    this.near = near;
  }

  destroy(): void {
    this.destroyed = true;
    this.listening.abort();
    cancelAnimationFrame(this.raf);
    this.io.disconnect();
    this.near?.disconnect();
    this.r.dispose();
    this.r.canvas.remove();
    this.flightBox.remove();
    for (const s of this.slots) {
      s.host.remove();
      delete s.el.dataset.state;
    }
  }

  // ------------------------------------------------------------ geometry

  /** Her figure box in viewport px: the render's box, or its crop in a frame. */
  private figureRect(s: SlotInfo): Rect {
    const b = s.figure.getBoundingClientRect();
    if (!s.framed) return { x: b.left, y: b.top, w: b.width, h: b.height };
    const c = this.rigs[this.drawable(s)].crop;
    return { x: b.left + c[0] * b.width, y: b.top + c[1] * b.height, w: c[2] * b.width, h: c[3] * b.height };
  }

  private visibility(s: SlotInfo): number {
    const r = this.figureRect(s);
    const vh = innerHeight;
    const vw = innerWidth;
    const w = Math.max(0, Math.min(r.x + r.w, vw) - Math.max(r.x, 0));
    const h = Math.max(0, Math.min(r.y + r.h, vh) - Math.max(r.y, 0));
    return r.w > 0 && r.h > 0 ? (w * h) / (r.w * r.h) : 0;
  }

  /** The slot you are reading: the most visible, if enough of it shows. */
  private pick(initial = false): SlotInfo | null {
    let best: SlotInfo | null = null;
    let score = 0;
    for (const s of this.slots) {
      if (!s.el.isConnected || s.figure.offsetParent === null) continue;
      const v = this.visibility(s) + (s === this.current ? 0.08 : 0);
      if (v > score) {
        best = s;
        score = v;
      }
    }
    if (best && score >= 0.34) return best;
    if (!initial) return null;
    // nothing on screen yet: the nearest one waits for you
    let near: SlotInfo | null = null;
    let dist = Infinity;
    for (const s of this.slots) {
      if (s.figure.offsetParent === null) continue;
      const r = this.figureRect(s);
      const d = Math.abs(r.y + r.h / 2 - innerHeight / 2);
      if (d < dist) {
        dist = d;
        near = s;
      }
    }
    return near;
  }

  // ------------------------------------------------------------ placement

  private place(s: SlotInfo): void {
    if (this.current && this.current !== s) this.current.el.dataset.state = "away";
    this.current = s;
    s.host.append(this.r.canvas);
    s.el.dataset.state = "live";
    // drawn in the same task as the static render goes, so no frame is empty
    this.render(performance.now() / 1000);
    this.onScreen = this.visibleSlots.has(s) || this.visibility(s) > 0;
    for (const fn of this.arriveListeners) fn(s.id);
  }

  private consider(now: number): void {
    if (this.flight || this.destroyed) return;
    const best = this.pick();
    if (!best || best === this.current) {
      this.candidate = null;
      return;
    }
    if (!this.r.has(this.drawable(best))) {
      // a texture that fails to arrive leaves her where she is
      void this.ensure(this.poseOf(best)).catch(() => undefined);
      return;
    }
    if (!this.candidate || this.candidate.slot !== best) this.candidate = { slot: best, since: now };
    const gone = !this.current || this.visibility(this.current) === 0;
    if (!gone && now - this.candidate.since < 0.14) return;
    this.candidate = null;
    this.go(best, now);
  }

  private go(to: SlotInfo, now: number): void {
    const from = this.current;
    const flinging = Math.abs(this.lastScroll.v) > 3200;
    if (!from || flinging || !this.r.has(HOP)) {
      this.place(to);
      return;
    }
    const a = this.figureRect(from);
    const b = this.figureRect(to);
    // Leaving from off screen, she drops in (or hops up) from the edge she
    // was beyond, already at the size she will land at: the part of the
    // flight nobody can see is not worth the time.
    let start = { ...a };
    let dropIn = false;
    if (a.y + a.h < 0 || a.y > innerHeight) {
      dropIn = true;
      const above = a.y + a.h < 0;
      const x = b.x + Math.max(-b.w * 1.2, Math.min(b.w * 1.2, (a.x - b.x) * 0.35));
      start = { x, y: above ? -b.h * 0.92 : innerHeight + b.h * 0.05, w: b.w, h: b.h };
    }
    const dist = Math.hypot(b.x - start.x, b.y - start.y);
    const dur = dropIn ? Math.min(0.66, 0.42 + dist / 3200) : Math.min(0.82, 0.5 + dist / 2600);
    from.el.dataset.state = "away";
    this.current = null;
    this.flightBox.append(this.r.canvas);
    this.flight = { from, to, start: now, dur, fromRect: start, dir: Math.sign(b.x - start.x) || 1, dropIn };
    this.brain.hopStart(now, dur, this.flight.dir);
    this.onScreen = true;
    // her first frame in the air now, so the box never shows at its last place
    this.render(now);
    this.wake();
  }

  // --------------------------------------------------------------- input

  private bind(): void {
    const now = () => performance.now() / 1000;
    const signal = this.listening.signal;
    addEventListener(
      "pointermove",
      (e) => {
        if (e.pointerType === "touch") return;
        this.brain.pointerMove(e.clientX, e.clientY, now());
        this.brain.setHover(this.hit(e.clientX, e.clientY));
        this.wake();
      },
      { passive: true, signal },
    );
    document.documentElement.addEventListener("pointerleave", () => this.brain.pointerLeave(), { signal });
    addEventListener("pointerdown", () => this.brain.input(now()), { passive: true, signal });
    // a poke is a click on her: a tap, never the start of a pan on a touch screen
    addEventListener(
      "click",
      (e) => {
        const t = e.target as Element;
        if (t.closest("a, button, input, textarea, select, label, [role=button], dialog")) return;
        if (!this.hit(e.clientX, e.clientY)) return;
        const face = this.faceScreen();
        this.brain.poke(e.clientX, e.clientY, now(), face?.x ?? e.clientX);
        this.wake();
      },
      { signal },
    );
    addEventListener(
      "scroll",
      () => {
        const t = now();
        const y = scrollY;
        const dy = y - this.lastScroll.y;
        const v = dy / Math.max(t - this.lastScroll.t, 1 / 240);
        this.lastScroll = { y, t, v: this.lastScroll.v * 0.6 + v * 0.4 };
        if (dy) this.brain.scrolled(dy, t);
        this.consider(t);
        this.wake();
      },
      { passive: true, signal },
    );
    addEventListener("resize", () => this.wake(), { passive: true, signal });
    document.addEventListener(
      "focusin",
      (e) => {
        const el = e.target as HTMLElement;
        if (!el?.getBoundingClientRect) return;
        const r = el.getBoundingClientRect();
        this.brain.focused(r.left + r.width / 2, r.top + r.height / 2, now());
        this.wake();
      },
      { signal },
    );
    addEventListener("keydown", () => this.brain.input(now()), { passive: true, signal });
    document.addEventListener(
      "visibilitychange",
      () => {
        if (document.hidden) {
          cancelAnimationFrame(this.raf);
          this.raf = 0;
        } else this.wake();
      },
      { signal },
    );
  }

  /** Is (x, y) on her? Her head's ellipse, or the column her body stands in. */
  hit(x: number, y: number): boolean {
    const s = this.current;
    if (!s || this.flight) return false;
    const d = this.destRect(s);
    const rig = this.rigs[this.drawable(s)];
    const u = (x - d.x) / d.w;
    const v = (y - d.y) / d.h;
    if (u < 0 || u > 1 || v < 0 || v > 1) return false;
    const aspect = rig.size[0] / rig.size[1];
    const hx = ((u - rig.head.cx) * aspect) / rig.head.rx;
    const hy = (v - rig.head.cy) / rig.head.ry;
    if (hx * hx + hy * hy < 1) return true;
    return v > rig.neck[1] && v < rig.ground && Math.abs(u - rig.hips[0]) < 0.3;
  }

  /** Where her face is on screen, and how big her head is there. */
  faceScreen(): { x: number; y: number; size: number } | null {
    const s = this.current;
    if (!s) return null;
    const d = this.destRect(s);
    const rig = this.rigs[this.drawable(s)];
    return { x: d.x + rig.face.cx * d.w, y: d.y + rig.face.cy * d.h, size: rig.head.ry * 2 * d.h };
  }

  /** Where the texture lands for a slot, in viewport px. */
  private destRect(s: SlotInfo): Rect {
    return this.figureRect(s);
  }

  // -------------------------------------------------------------- render

  wake(): void {
    if (this.destroyed || document.hidden || this.raf) return;
    this.raf = requestAnimationFrame((t) => this.tick(t));
  }

  private tick(t: number): void {
    this.raf = 0;
    const now = t / 1000;
    // 60 frames a second is all breathing and blinking need; a 120 Hz screen
    // gets every other frame, and half the work
    if (this.last && now - this.last < 1 / 64) {
      this.wake();
      return;
    }
    const dt = this.last ? now - this.last : 1 / 60;
    this.last = now;
    this.consider(now);
    // drawn only when she can be seen or is in the air; a pointer moving
    // while she is off screen wakes the loop to look, not to draw
    const busy = Boolean(this.flight || (this.current && (this.onScreen || this.visibility(this.current) > 0)));
    if (busy) {
      this.render(now, dt);
      this.wake();
    }
  }

  private render(now: number, dt = 0): void {
    if (this.r.lost) return;
    if (this.flight) return this.renderFlight(now, dt);
    const s = this.current;
    if (!s) return;
    const host = s.host.getBoundingClientRect();
    if (host.width < 2 || host.height < 2) return;
    this.r.resize(host.width, host.height);
    const pose = this.drawable(s);
    const rig = this.rigs[pose];
    const face = this.faceScreen()!;
    const body: Body = { kind: s.kind, restGaze: rig.gaze, limbs: rig.limbs.map((l) => l.id), face };
    const f = this.brain.step(now, dt, body);
    this.frame = f;
    const d = this.destRect(s);
    this.r.clear();
    this.r.draw(pose, f, { x: d.x - host.left, y: d.y - host.top, w: d.w, h: d.h });
  }

  private renderFlight(now: number, dt: number): void {
    const fl = this.flight!;
    const t = Math.min(1, (now - fl.start) / fl.dur);
    const to = this.figureRect(fl.to);
    const a = fl.fromRect;
    // an arc: up and over, higher for a longer hop, landing on the new place
    const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    // a hop between two places arcs over; a drop from above barely rises
    const falling = fl.dropIn && a.y < to.y;
    const lift = falling ? 18 : Math.min(160, 40 + Math.hypot(to.x - a.x, to.y - a.y) * 0.18);
    const cx = a.x + a.w / 2 + (to.x + to.w / 2 - (a.x + a.w / 2)) * e;
    const bottom = a.y + a.h + (to.y + to.h - (a.y + a.h)) * e - Math.sin(Math.PI * t) * lift;
    const h = a.h + (to.h - a.h) * e;
    const boxH = Math.max(a.h, to.h) * 1.12;
    const boxW = boxH * 0.8;
    this.r.resize(boxW, boxH);
    this.r.clear();
    // which body: hers as she leaves, the hop in the air, hers as she lands
    // (dropping in from off screen, she is already in the air)
    const src = fl.from ? this.drawable(fl.from) : this.drawable(fl.to);
    const dst = this.drawable(fl.to);
    const phase = t < (fl.dropIn ? 0 : 0.2) ? 0 : t < (fl.dropIn ? 0.72 : 0.78) ? 1 : 2;
    const pose = phase === 0 ? src : phase === 1 ? HOP : dst;
    const rig = this.rigs[pose];
    const kind: Kind = phase === 1 ? "hop" : phase === 0 ? (fl.from?.kind ?? "stand") : fl.to.kind;
    const aspect = rig.size[0] / rig.size[1];
    const ph = phase === 1 ? h * 0.92 : h;
    const pw = ph * aspect;
    const face = { x: cx, y: bottom - ph + rig.face.cy * ph, size: rig.head.ry * 2 * ph };
    const f = this.brain.step(now, dt, {
      kind,
      restGaze: [fl.dir * 0.3, 0.2],
      limbs: rig.limbs.map((l) => l.id),
      face,
    });
    this.frame = f;
    // the canvas box rides the arc; the pose sits on its floor
    const left = cx - boxW / 2;
    const top = bottom - boxH;
    const lean = Math.sin(Math.PI * t) * 0.09 * fl.dir;
    this.flightBox.style.transform = `translate(${left}px, ${top}px) rotate(${lean}rad)`;
    this.flightBox.style.width = `${boxW}px`;
    this.flightBox.style.height = `${boxH}px`;
    this.r.draw(pose, f, { x: (boxW - pw) / 2, y: boxH - ph, w: pw, h: ph });
    if (t >= 1) {
      this.flight = null;
      this.flightBox.style.transform = "";
      this.flightBox.style.width = "0px";
      this.flightBox.style.height = "0px";
      this.place(fl.to);
      this.brain.hopLand(now);
    }
  }
}
