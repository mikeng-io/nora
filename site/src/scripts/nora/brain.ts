// Her behaviour: what she looks at, when she blinks, how she idles, what
// she does when poked. It knows nothing about pixels; it turns what is
// happening on the page into one Frame (types.ts) a pose can be drawn in.
//
// Her character is the spec (design/self-reference/05): calm, observant, a
// little deadpan, never over-acted. So everything here is small. Her eyes
// jump and hold, the way eyes do, and her head follows them slowly; she
// glances at your pointer and then gets bored of it; left alone she listens
// to her music, and left alone for long enough she dozes off. Poke her and
// she notices, then tilts her head, then gives you a look, then ignores you.
//
// There is one brain for the one of her: its mood survives her moving from
// the hero to the chat window, so an annoyed Nora is still annoyed there.
import { restFrame, type Frame } from "./types";

export type Kind = "stand" | "perch" | "ledge" | "quiet" | "wave" | "hop" | "lost";

export type Mood = "awake" | "music" | "doze" | "thinking" | "annoyed" | "speaking";

/** What the brain needs to know about the body it drives this frame. */
export interface Body {
  kind: Kind;
  /** Where the pose's own eyes look (her activity), gaze units. */
  restGaze: [number, number];
  /** Limb ids in the pose's order. */
  limbs: string[];
  /** Her face on screen, viewport px, and the size of her head there. */
  face: { x: number; y: number; size: number };
}

export type Cue =
  | "tall"
  | "bun"
  | "eyes"
  | "cheeks"
  | "hoodie"
  | "earphones"
  | "jeans"
  | "sneakers"
  | "open"
  | "unimpressed"
  | "avoid"
  | "hush"
  | "nod";

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const ease = (t: number) => t * t * (3 - 2 * t);

/** A damped spring on one value. Substepped, so a slow frame stays stable. */
class Spring {
  v = 0;
  constructor(
    public x = 0,
    public omega = 10,
    public zeta = 1,
  ) {}
  step(target: number, dt: number): number {
    let t = dt;
    while (t > 0) {
      const h = Math.min(t, 1 / 120);
      this.v += (this.omega * this.omega * (target - this.x) - 2 * this.zeta * this.omega * this.v) * h;
      this.x += this.v * h;
      t -= h;
    }
    return this.x;
  }
  kick(dv: number): void {
    this.v += dv;
  }
}

class Spring2 {
  a: Spring;
  b: Spring;
  constructor(omega: number, zeta: number, x = 0, y = 0) {
    this.a = new Spring(x, omega, zeta);
    this.b = new Spring(y, omega, zeta);
  }
  step(tx: number, ty: number, dt: number): [number, number] {
    return [this.a.step(tx, dt), this.b.step(ty, dt)];
  }
  kick(dx: number, dy: number): void {
    this.a.kick(dx);
    this.b.kick(dy);
  }
}

interface Look {
  x: number;
  y: number;
  until: number;
}

interface Reaction {
  kind: "notice" | "tilt" | "look" | "ignore" | "startle" | "cue";
  start: number;
  end: number;
  side: number;
  cue?: Cue;
}

export class Brain {
  // what is happening on the page
  private pointer: { x: number; y: number; t: number } | null = null;
  private hover = false;
  private scroll = { dir: 0, t: -10 };
  private focus: { x: number; y: number; t: number } | null = null;
  private look: Look | null = null;
  private lastInput = 0;
  private attendSince = 0;
  private boredUntil = 0;
  private pokes: number[] = [];
  private reaction: Reaction | null = null;
  private annoyance = 0;
  private thinking = false;
  private speaking = 0;
  private dark = false;
  private lateHour = false;
  private stillUntil = 0;
  private waveUntil = -1;
  private waveStart = 0;
  private hop: { start: number; land: number; dir: number } | null = null;
  private landAt = -10;

  // her own rhythms
  private breathPhase = rand(0, 6);
  private blinkAt = 1.2;
  private blinkStart = -10;
  private blinkDur = 0.24;
  private doubleBlink = false;
  private saccadeAt = 0;
  private saccade: [number, number] = [0, 0];
  private glanceAt = rand(6, 11);
  private glanceUntil = -1;
  private glance: [number, number] = [0, 0];
  private flickAt = rand(2, 4);
  private writing = { on: true, until: rand(0.8, 1.8) };
  private fidgetAt = rand(6, 10);
  private catchAt = 0;
  private caughtUntil = -1;
  private mode: "awake" | "music" | "doze" = "awake";
  private started = -1;
  private nodAt = -1;
  private yawnAt = -1;

  // springs, one per channel
  private gaze = new Spring2(34, 1);
  private headFollow = new Spring2(6.5, 1);
  private roll = new Spring(0, 7, 0.9);
  private nod = new Spring2(9, 0.8);
  private lidExpr = new Spring(0, 9, 1);
  private flat = new Spring(0, 9, 1);
  private tilt = new Spring(0, 9, 1);
  private lash = new Spring(0, 10, 1);
  private low = new Spring(0, 10, 1);
  private mouthO = new Spring(0, 16, 0.9);
  private smile = new Spring(0, 8, 1);
  private blush = new Spring(0, 3, 1);
  private hairLag = new Spring2(14, 0.32);
  private squash = new Spring2(20, 0.38, 1, 1);
  private limbs = [new Spring(0, 14, 0.7), new Spring(0, 14, 0.7), new Spring(0, 14, 0.7)];
  private limbShift = [new Spring2(18, 0.8), new Spring2(18, 0.8), new Spring2(18, 0.8)];

  // ------------------------------------------------------------- inputs

  input(now: number): void {
    if (this.mode === "doze") this.startle(now);
    this.mode = "awake";
    this.lastInput = now;
  }

  pointerMove(x: number, y: number, now: number): void {
    const moved = !this.pointer || Math.hypot(x - this.pointer.x, y - this.pointer.y) > 3;
    if (!moved) return;
    if (!this.pointer || now - this.pointer.t > 2.2) this.attendSince = now;
    this.pointer = { x, y, t: now };
    this.input(now);
  }

  pointerLeave(): void {
    this.pointer = null;
    this.hover = false;
  }

  setHover(on: boolean): void {
    this.hover = on;
  }

  scrolled(dir: number, now: number): void {
    this.scroll = { dir: Math.sign(dir), t: now };
    this.input(now);
  }

  focused(x: number, y: number, now: number): void {
    this.focus = { x, y, t: now };
    this.input(now);
  }

  /** Look at a point on the screen (a message arriving, a label) for `dur` s. */
  attend(x: number, y: number, dur: number, now: number): void {
    this.look = { x, y, until: now + dur };
    this.maybeBlink(now, 0.35);
  }

  setThinking(on: boolean): void {
    this.thinking = on;
  }

  /** 0..1, from the loudness of her voice while a clip plays. */
  setSpeaking(level: number): void {
    this.speaking = level;
  }

  /** The lighting changed: she blinks with it; late night makes her drowsy,
      and the lamp going down gets one small yawn. */
  setLight(dark: boolean, now: number): void {
    if (dark !== this.dark && this.started >= 0) {
      this.blink(now, 0.26);
      if (dark) this.yawnAt = now + 0.55;
    }
    this.dark = dark;
  }

  /** Night in Hong Kong, where she lives. */
  setLateHour(on: boolean): void {
    this.lateHour = on;
  }

  /** Deadpan: hold perfectly still but for one slow blink. */
  holdStill(dur: number, now: number): void {
    this.stillUntil = now + dur;
    this.blink(now + 0.18, 0.36);
  }

  /** Writing something down in her notebook (the memory section), head down. */
  write(now: number, dur: number): void {
    this.writing = { on: true, until: now + dur };
    this.look = null;
    this.glanceUntil = -1;
    this.input(now);
  }

  wave(now: number): void {
    this.waveStart = now;
    this.waveUntil = now + 1.9;
  }

  hopStart(now: number, dur: number, dir: number): void {
    this.hop = { start: now, land: now + dur, dir };
    this.squash.kick(0.9, -1.4);
    this.hairLag.kick(0, 0.06);
    this.input(now);
  }

  hopLand(now: number): void {
    this.hop = null;
    this.landAt = now;
    this.squash.kick(1.6, -2.2);
    this.hairLag.kick(0, 0.09);
    this.blink(now + 0.08, 0.22);
    this.glanceUntil = now + 1.2;
    this.glance = [0, 0];
  }

  poke(x: number, y: number, now: number, faceX: number): void {
    this.input(now);
    this.pokes = this.pokes.filter((t) => now - t < 4.5);
    this.pokes.push(now);
    const n = this.pokes.length;
    const side = x < faceX ? -1 : 1;
    this.pointer = { x, y, t: now };
    this.squash.kick(0.7, -1.1);
    this.hairLag.kick(side * 0.03, 0.04);
    if (n === 1) {
      this.reaction = { kind: "notice", start: now, end: now + 1.3, side };
      this.blink(now + 0.05, 0.22);
      this.blush.kick(1.2);
    } else if (n === 2) {
      this.reaction = { kind: "tilt", start: now, end: now + 1.5, side };
      this.blink(now + 0.1, 0.4);
    } else if (n === 3) {
      this.reaction = { kind: "look", start: now, end: now + 2.2, side };
      this.annoyance = 1;
    } else {
      this.reaction = { kind: "ignore", start: now, end: now + 2.6, side };
      this.annoyance = 1;
    }
  }

  /** A named, small action: the form plate's anchors, the manners list. */
  cue(cue: Cue, now: number, side = 1): void {
    this.input(now);
    this.reaction = { kind: "cue", start: now, end: now + (cue === "open" || cue === "avoid" ? 1.8 : 1.3), side, cue };
    if (cue === "eyes") this.blink(now + 0.1, 0.42);
    if (cue === "bun") this.hairLag.kick(0.05 * side, -0.08);
    if (cue === "tall") this.squash.kick(-0.6, 1.2);
    if (cue === "cheeks") this.blush.kick(2.2);
    if (cue === "hoodie") this.squash.kick(1.0, -0.4);
    if (cue === "nod") {
      this.nod.kick(0, 0.08);
      this.blink(now + 0.12, 0.28);
    }
  }

  get mood(): Mood {
    if (this.speaking > 0.02) return "speaking";
    if (this.thinking) return "thinking";
    if (this.annoyance > 0.35) return "annoyed";
    return this.mode;
  }

  // ------------------------------------------------------------ rhythms

  private blink(at: number, dur: number): void {
    this.blinkStart = at;
    this.blinkDur = dur;
    this.blinkAt = at + dur + rand(1.4, 3.2);
  }

  private maybeBlink(now: number, p: number): void {
    if (Math.random() < p && now - this.blinkStart > 0.5) this.blink(now, 0.22);
  }

  private startle(now: number): void {
    this.reaction = { kind: "startle", start: now, end: now + 0.9, side: 1 };
    this.blink(now, 0.16);
    this.doubleBlink = true;
    this.mouthO.kick(3);
  }

  private sleepy(): boolean {
    return this.dark || this.lateHour;
  }

  /** How closed the blink has her lids at `now`, 0..1. */
  private blinkLid(now: number): number {
    const t = now - this.blinkStart;
    const d = this.blinkDur;
    if (t < 0 || t > d) {
      if (this.doubleBlink && t > d && t < d + 0.12) return 0;
      if (this.doubleBlink && t >= d + 0.12) {
        this.doubleBlink = false;
        this.blinkStart = now;
        this.blinkDur = d * 0.9;
      }
      return 0;
    }
    const close = d * 0.32;
    const hold = d * 0.14;
    if (t < close) return ease(t / close);
    if (t < close + hold) return 1;
    return 1 - ease((t - close - hold) / (d - close - hold));
  }

  // --------------------------------------------------------------- step

  step(now: number, dt: number, body: Body): Frame {
    if (this.started < 0) {
      this.started = now;
      this.lastInput = now;
      // coming alive: a slow blink and the smallest nod, hello
      if (body.kind === "stand") {
        this.blink(now + 0.9, 0.34);
        this.nodAt = now + 1.35;
      }
    }
    if (this.nodAt > 0 && now >= this.nodAt) {
      this.nodAt = -1;
      this.nod.kick(0, 0.06);
    }
    dt = clamp(dt, 0, 1 / 20);
    const f = restFrame();
    f.time = now;
    const k = body.kind;
    const standing = k === "stand" || k === "wave" || k === "quiet" || k === "lost";
    const busy = k === "perch" || k === "ledge";
    const closedAtRest = k === "quiet";

    // idle: music after a while, then dozing off; later if she is busy
    const idle = now - this.lastInput;
    const dozeAfter = this.sleepy() ? 18 : 34;
    if (this.mode !== "doze" && idle > dozeAfter + (busy ? 16 : 0) && !this.speaking && k !== "hop") {
      this.mode = "doze";
      this.catchAt = now + rand(9, 14);
    } else if (this.mode === "awake" && idle > 9 && standing) {
      this.mode = "music";
    }
    if (now > (this.reaction?.end ?? -1)) this.reaction = null;
    if (this.look && now > this.look.until) this.look = null;
    this.annoyance = Math.max(0, this.annoyance - dt / (now - (this.pokes.at(-1) ?? -99) < 1.5 ? 99 : 5));

    const r = this.reaction;
    const doze = this.mode === "doze";
    const music = this.mode === "music";
    const still = now < this.stillUntil;

    // ---- where she looks
    const toGaze = (x: number, y: number): [number, number] => {
      const s = Math.max(body.face.size, 40);
      return [Math.tanh((x - body.face.x) / (s * 3.2)), Math.tanh((y - body.face.y) / (s * 2.6))];
    };
    let target: [number, number] = [...body.restGaze];
    const pointerFresh = this.pointer && now - this.pointer.t < 2.2;
    if (pointerFresh && now - this.attendSince > rand(5.5, 8) && now > this.boredUntil + 1) {
      this.boredUntil = now + rand(1.8, 3);
      this.attendSince = now + 3;
    }
    if (r?.kind === "ignore") {
      target = [-r.side * 0.9, 0.15];
    } else if (r && r.kind !== "cue" && this.pointer) {
      target = toGaze(this.pointer.x, this.pointer.y);
    } else if (r?.kind === "cue") {
      target = this.cueGaze(r.cue!, body.restGaze);
    } else if (this.look) {
      target = toGaze(this.look.x, this.look.y);
    } else if (this.thinking) {
      target = [-0.5 + 0.12 * Math.sin(now * 0.9), -0.7 + 0.08 * Math.sin(now * 1.3)];
    } else if (this.hover && this.pointer) {
      target = toGaze(this.pointer.x, this.pointer.y);
    } else if (pointerFresh && now > this.boredUntil && !busy) {
      target = toGaze(this.pointer!.x, this.pointer!.y);
    } else if (pointerFresh && busy && now > this.boredUntil && Math.random() < 0.004) {
      // busy with her phone or her notes, she only glances at the pointer
      this.glanceUntil = now + rand(0.7, 1.2);
      this.glance = toGaze(this.pointer!.x, this.pointer!.y);
    } else if (this.focus && now - this.focus.t < 1.8) {
      target = toGaze(this.focus.x, this.focus.y);
    } else if (now - this.scroll.t < 0.45 && !busy) {
      target = [target[0] * 0.5, 0.75 * this.scroll.dir];
    }
    if (now < this.glanceUntil && !r && !this.look && !this.thinking) target = this.glance;
    if (!r && !this.look && !this.hover && !pointerFresh && now > this.glanceAt) {
      // left to herself: a glance up at you if she was looking away, away if she was looking at you
      const atYou = Math.abs(body.restGaze[0]) + Math.abs(body.restGaze[1]) < 0.2;
      this.glance = atYou ? [rand(-0.9, 0.9), rand(0.1, 0.6)] : [rand(-0.1, 0.1), rand(-0.1, 0.05)];
      this.glanceUntil = now + rand(0.9, 1.7);
      this.glanceAt = now + rand(7, 13);
    }
    // micro-saccades: the eyes never sit perfectly still
    if (now > this.saccadeAt && !still) {
      this.saccade = [rand(-0.1, 0.1), rand(-0.07, 0.07)];
      this.saccadeAt = now + rand(0.6, 2.2);
    }
    if (k === "lost" && !r && !this.look && !(pointerFresh && now > this.boredUntil)) {
      // still looking for it: up, and slowly from side to side
      target = [0.55 * Math.sin(now * 0.42), -0.3 + 0.18 * Math.sin(now * 0.23 + 1)];
    }
    if (doze) target = [0, 0.55];
    if (k === "hop" && this.hop) target = [this.hop.dir * 0.35, 0.3];
    const tx = clamp(target[0] + (still ? 0 : this.saccade[0]), -1, 1);
    const ty = clamp(target[1] + (still ? 0 : this.saccade[1]), -1, 1);
    const prev = [this.gaze.a.x, this.gaze.b.x];
    if (Math.hypot(tx - prev[0], ty - prev[1]) > 0.9) this.maybeBlink(now, 0.4);
    const g = this.gaze.step(tx, ty, dt);
    f.gaze = [g[0], g[1]];
    const hf = this.headFollow.step(g[0], g[1], dt);
    f.turn = [hf[0] * 0.012, hf[1] * 0.007];

    // ---- head
    let rollT = -hf[0] * 0.02 + 0.008 * Math.sin(now * 0.21 + 1.3);
    let nodY = 0;
    if (r?.kind === "tilt") rollT += 0.075 * r.side;
    if (r?.kind === "notice") rollT += 0.02 * r.side;
    if (r?.kind === "ignore") rollT -= 0.05 * r.side;
    if (r?.kind === "cue" && r.cue === "earphones") rollT += 0.07;
    if (r?.kind === "cue" && (r.cue === "jeans" || r.cue === "sneakers" || r.cue === "hoodie")) nodY += 0.006;
    if (this.thinking) rollT -= 0.035;
    if (music) {
      const beat = now * 1.75;
      nodY += 0.0028 * Math.pow(Math.max(0, Math.sin(beat * Math.PI * 2)), 2);
      rollT += 0.014 * Math.sin(beat * Math.PI);
    }
    if (doze) {
      rollT += 0.045;
      nodY += 0.009;
      if (now > this.catchAt) {
        // catching herself: head up, eyes half open, and back down
        this.catchAt = now + rand(10, 16);
        this.caughtUntil = now + 0.9;
        this.nod.kick(0, -0.07);
      }
    }
    if (still) rollT = this.roll.x;
    f.roll = this.roll.step(rollT, dt);
    const nd = this.nod.step(0, nodY, dt);
    f.nod = [nd[0], nd[1]];

    // ---- lids and mouth
    let lidT = 0;
    let flatT = 0;
    let tiltT = 0;
    let lashT = 0;
    let lowT = 0;
    let smileT = 0;
    let mouthT = 0;
    if (this.sleepy()) {
      lidT = 0.16;
      flatT = 0.9;
    }
    if (music) {
      lidT = Math.max(lidT, 0.24);
      flatT = 0.8;
    }
    if (this.annoyance > 0.35 || r?.kind === "look" || r?.kind === "ignore") {
      lidT = 0.48;
      flatT = 1;
      tiltT = -0.1;
      lashT = 1;
      smileT = -0.25;
    }
    if (this.thinking) {
      lidT = Math.max(lidT, 0.12);
      flatT = Math.max(flatT, 0.7);
    }
    if (doze) {
      lidT = now < this.caughtUntil ? 0.5 : 0.93;
      flatT = 1;
    }
    if (closedAtRest) {
      // her listening pose: eyes closed unless something opens them
      lidT = 1;
      flatT = 0;
      if (
        r &&
        (r.kind === "notice" || r.kind === "tilt" || r.kind === "startle" || r.cue === "open" || r.cue === "avoid")
      ) {
        lidT = 0;
      }
      if (r?.kind === "look" || r?.kind === "ignore" || r?.cue === "unimpressed") {
        lidT = 0.48;
        flatT = 1;
        lashT = 1;
      }
      if (r?.cue === "hush") lidT = 1;
    } else if (r?.cue === "unimpressed") {
      lidT = 0.48;
      flatT = 1;
      tiltT = -0.1;
      lashT = 1;
    }
    if (k === "lost" && !r) {
      // unimpressed, as in her render: the page is not here, and she looked
      lidT = Math.max(lidT, 0.44);
      flatT = 1;
      tiltT = 0;
      lashT = 1;
    }
    if (r?.kind === "startle") mouthT = 0.35;
    if (this.speaking > 0.02) mouthT = clamp(this.speaking, 0, 1) * 0.85;
    if (doze && Math.sin(now * 0.35) > 0.96) mouthT = Math.max(mouthT, 0.5); // the occasional yawn
    const yawn = this.yawnAt > 0 ? (now - this.yawnAt) / 1.5 : -1;
    if (yawn >= 0 && yawn <= 1) {
      const y = Math.sin(Math.PI * yawn);
      mouthT = Math.max(mouthT, 0.6 * y);
      lidT = Math.max(lidT, 0.6 * y);
      flatT = Math.max(flatT, 0.8);
    } else if (yawn > 1) this.yawnAt = -1;

    const eLid = this.lidExpr.step(lidT, dt);
    const eFlat = this.flat.step(flatT, dt);
    const b = closedAtRest && lidT >= 0.99 ? 0 : this.blinkLid(now);
    if (now > this.blinkAt && !doze && !(closedAtRest && lidT > 0.9)) {
      const slow = this.sleepy() || music;
      this.blink(now, slow ? rand(0.3, 0.42) : rand(0.2, 0.27));
      this.doubleBlink = Math.random() < 0.12;
      this.blinkAt = now + (slow ? rand(3.5, 7) : rand(1.8, 5.5));
    }
    f.lid = Math.max(eLid, b);
    f.flat = eFlat * (1 - b);
    f.tilt = this.tilt.step(tiltT, dt);
    f.lash = this.lash.step(lashT, dt) * (1 - b);
    f.low = this.low.step(lowT, dt);
    f.eyeScale = [1 + 0.12 * f.lid * (1 - f.flat), 1];
    f.mouthO = clamp(this.mouthO.step(mouthT, dt), 0, 1);
    f.smile = this.smile.step(smileT, dt);
    f.blush = clamp(this.blush.step(0, dt), 0, 1);

    // ---- body
    const period = doze ? 6.4 : music ? 5.2 : 4.4;
    this.breathPhase += (dt * Math.PI * 2) / period;
    const amp = (standing ? 0.0032 : 0.0026) * (doze ? 1.35 : 1);
    f.breath = amp * (0.5 - 0.5 * Math.cos(this.breathPhase));
    f.sway = (standing ? 0.0075 : 0.003) * Math.sin(now * 0.31 + 1.2) * Math.sin(now * 0.17 + 0.4);
    const lag = this.hairLag.step(-this.roll.v * 0.01, -this.nod.b.v * 0.6, dt);
    f.hairLag = [lag[0], lag[1]];
    f.hairDrift = k === "hop" ? 0.004 : 0.0011;
    const sq = this.squash.step(1, 1, dt);
    f.squash = [sq[0], sq[1]];

    // ---- limbs, by what the pose is doing
    const rot = [0, 0, 0];
    const shift: [number, number][] = [
      [0, 0],
      [0, 0],
      [0, 0],
    ];
    // looking up from what her hands are doing
    const lookingUp = Boolean(r || this.look || this.thinking || now < this.glanceUntil);
    body.limbs.forEach((id, i) => {
      if (id === "wave") {
        if (now < this.waveUntil) {
          const t = (now - this.waveStart) / (this.waveUntil - this.waveStart);
          rot[i] = 0.17 * Math.sin((now - this.waveStart) * Math.PI * 2 * 2.3) * Math.sin(Math.PI * clamp(t, 0, 1));
        }
      } else if (id === "phone") {
        if (now > this.flickAt && !lookingUp && !doze) {
          this.limbShift[i].kick(0, -0.05);
          this.limbs[i].kick(-0.35);
          this.flickAt = now + rand(2.4, 5);
        }
        rot[i] = lookingUp ? 0.05 : 0;
        if (doze) rot[i] = 0.09;
      } else if (id === "pencil") {
        if (now > this.writing.until) {
          this.writing = { on: !this.writing.on, until: now + (this.writing.on ? rand(0.4, 1.4) : rand(0.8, 2)) };
        }
        if (this.writing.on && !lookingUp && !doze) {
          rot[i] = 0.035 * Math.sin(now * Math.PI * 2 * 4.6) + 0.02 * Math.sin(now * 2.1);
          shift[i] = [0.004 * (((now * 0.9) % 1) - 0.5), 0];
        }
      } else if (id === "legs") {
        const swing = doze ? 0 : lookingUp ? 0.35 : 1;
        rot[i] = 0.05 * swing * Math.sin(now * Math.PI * 2 * 0.55);
      } else if (id === "scratch") {
        // scratching her head, in short bursts
        const burst = Math.sin(now * 0.8) > 0.55 && !doze;
        rot[i] = burst ? 0.05 * Math.sin(now * Math.PI * 2 * 4.2) : 0;
      } else if (id === "cable") {
        if (now > this.fidgetAt) {
          this.limbs[i].kick(0.5);
          this.fidgetAt = now + rand(6, 11);
        }
      } else if (id === "armL" || id === "armR") {
        const s = id === "armL" ? 1 : -1;
        if (this.hop) {
          const t = clamp((now - this.hop.start) / (this.hop.land - this.hop.start), 0, 1);
          rot[i] = s * (0.28 * Math.sin(Math.PI * t) - 0.08);
        }
      }
    });
    f.limbRot = rot.map((v, i) => this.limbs[i].step(v, dt));
    f.limbShift = shift.map((s, i) => this.limbShift[i].step(s[0], s[1], dt));

    // a landing settles for a moment before she looks around
    if (now - this.landAt < 0.25) f.gaze = [f.gaze[0] * 0.5, f.gaze[1] * 0.5 + 0.2];
    return f;
  }

  private cueGaze(cue: Cue, rest: [number, number]): [number, number] {
    switch (cue) {
      case "tall":
      case "bun":
        return [0.1, -0.9];
      case "eyes":
      case "cheeks":
      case "open":
      case "unimpressed":
        return [0, 0];
      case "hoodie":
      case "jeans":
        return [-0.2, 0.9];
      case "sneakers":
        return [0.1, 1];
      case "earphones":
        return [0.6, 0.1];
      case "avoid":
        return [0.95, -0.1];
      case "hush":
        return rest;
      case "nod":
        return [0, 0];
    }
  }
}
