// What connects her to the page around her: the lighting, the hour where
// she lives, the status beside her name in the header, and each section's
// own moments (the chat demo, the photo plate, the portal, the manners).
import type { RoomEvent } from "../chat";
import type { Cue } from "./brain";
import type { Stage } from "./stage";

const now = () => performance.now() / 1000;

/** The status beside her name: where she is, unless her mood says more. */
const SLOT_STATUS: Record<string, string> = {
  hero: "listening",
  room: "reading",
  memory: "writing",
  manners: "quiet",
  form: "posing",
  portal: "waving",
  lost: "searching",
};

export function connect(stage: Stage, presence: Record<string, string>): () => void {
  const root = document.documentElement;
  const off: (() => void)[] = [];
  const brain = stage.brain;

  // ---- lighting: she blinks with the switch, and late night makes her drowsy
  const system = matchMedia("(prefers-color-scheme: dark)");
  const dark = () => (root.dataset.theme ? root.dataset.theme === "dark" : system.matches);
  stage.setDark(dark(), now());
  const mo = new MutationObserver(() => stage.setDark(dark(), now()));
  mo.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  const sys = () => stage.setDark(dark(), now());
  system.addEventListener("change", sys);
  off.push(
    () => mo.disconnect(),
    () => system.removeEventListener("change", sys),
  );

  // ---- the hour in Hong Kong, where she lives: after one she is up late
  const hour = () =>
    Number(
      new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Hong_Kong", hour: "numeric", hourCycle: "h23" }).format(
        new Date(),
      ),
    );
  const late = () => {
    const h = hour();
    brain.setLateHour(h >= 1 && h < 6);
  };
  late();
  const clock = setInterval(late, 60_000);
  off.push(() => clearInterval(clock));

  // ---- the status beside her name, and the hero's caption while she is out
  const status = document.querySelector<HTMLElement>("[data-nora-status]");
  const statusText = status?.querySelector<HTMLElement>("[data-nora-status-text]");
  const caption = document.querySelector<HTMLElement>("[data-nora-away]");
  const captionHome = caption?.textContent ?? "";
  let shown = "";
  const tick = () => {
    const slot = stage.slot?.id;
    const mood = brain.mood;
    let key = slot ? (SLOT_STATUS[slot] ?? "listening") : "moving";
    if (mood === "doze") key = "dozing";
    else if (mood === "annoyed") key = "annoyed";
    else if (mood === "thinking") key = "thinking";
    else if (mood === "speaking") key = "speaking";
    else if (mood === "music" && (slot === "hero" || slot === "manners")) key = "music";
    const text = presence[key] ?? "";
    if (status && statusText && text !== shown) {
      shown = text;
      status.hidden = false;
      status.classList.remove("changed");
      void status.offsetWidth;
      status.classList.add("changed");
      statusText.textContent = text;
    }
    if (caption) {
      const away = slot !== "hero";
      const want = away ? presence.away : captionHome;
      if (caption.textContent !== want) caption.textContent = want;
    }
  };
  tick();
  const statusTimer = setInterval(tick, 300);
  off.push(() => {
    clearInterval(statusTimer);
    if (status) status.hidden = true;
    if (caption) caption.textContent = captionHome;
  });

  // her voice clip moves her mouth; your earphones going in gets a nod
  const voice = (e: Event) => {
    brain.setSpeaking((e as CustomEvent<{ level: number }>).detail.level);
    stage.wake();
  };
  const earphones = (e: Event) => {
    if (!(e as CustomEvent<{ on: boolean }>).detail.on) return;
    brain.cue("nod", now());
    stage.wake();
  };
  document.addEventListener("nora:voice", voice);
  document.addEventListener("nora:earphones", earphones);
  off.push(() => {
    document.removeEventListener("nora:voice", voice);
    document.removeEventListener("nora:earphones", earphones);
  });

  off.push(room(stage), portal(stage), plate(stage), manners(stage), notebook(stage));
  return () => off.forEach((fn) => fn());
}

const centre = (el: Element) => {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
};

/** The chat demo: she looks up at what you asked, thinks, reads her answer
    back, and holds perfectly still while the room changes around her. */
function room(stage: Stage): () => void {
  const brain = stage.brain;
  const on = (e: Event) => {
    const d = (e as CustomEvent<RoomEvent>).detail;
    const t = now();
    if (d.kind === "think") brain.setThinking(Boolean(d.thinking));
    if (stage.slot?.id !== "room") return;
    if ((d.kind === "ask" || d.kind === "reply") && d.el) {
      const c = centre(d.el.querySelector(".body") ?? d.el);
      brain.attend(c.x, c.y, d.kind === "ask" ? 1.0 : 1.3, t);
    }
    if (d.kind === "skin") brain.holdStill(1.5, t);
    stage.wake();
  };
  document.addEventListener("nora:room", on);
  return () => document.removeEventListener("nora:room", on);
}

/** The portal: a small wave when she gets there, and again when you go for the door. */
function portal(stage: Stage): () => void {
  let last = 0;
  const wave = () => {
    const t = now();
    if (stage.slot?.id !== "portal" || t - last < 2.4) return;
    last = t;
    stage.brain.wave(t);
    stage.wake();
  };
  stage.onArrive((id) => {
    if (id === "portal") setTimeout(wave, 420);
  });
  const cta = document.querySelector<HTMLElement>("#portal .btn-primary");
  cta?.addEventListener("pointerenter", wave);
  cta?.addEventListener("focus", wave);
  return () => {
    cta?.removeEventListener("pointerenter", wave);
    cta?.removeEventListener("focus", wave);
  };
}

// The eight anchors, in the order of the plate's labels (Form.astro).
const ANCHOR_CUES: Cue[] = ["tall", "bun", "eyes", "cheeks", "hoodie", "earphones", "jeans", "sneakers"];

/** The photo plate: the first time she steps onto her mark, each anchor is
    pointed out in turn and she glances at it; pointing at a label later
    gets the same glance, and a small show of that part of her. */
function plate(stage: Stage): () => void {
  const labels = [...document.querySelectorAll<HTMLElement>("#form .label-item")];
  const pins = [...document.querySelectorAll<HTMLElement>("#form .pin")];
  if (!labels.length) return () => undefined;
  // label i points at pin (i - 1): the first anchor, her proportions, is the ruler
  const pinFor = (i: number) => (i === 0 ? null : (pins[i - 1] ?? null));
  const light = (i: number, on: boolean) => {
    labels[i]?.classList.toggle("lit", on);
    pinFor(i)?.classList.toggle("lit", on);
    document.querySelector(`#form .lines [data-anchor="${i}"]`)?.classList.toggle("lit", on);
  };
  const show = (i: number, dur = 0.9) => {
    const t = now();
    if (stage.slot?.id !== "form") return;
    const pin = pinFor(i);
    if (pin && matchMedia("(min-width: 1024px)").matches) {
      const c = centre(pin);
      stage.brain.attend(c.x, c.y, dur, t);
    }
    stage.brain.cue(ANCHOR_CUES[i], t);
    stage.wake();
  };
  let toured = false;
  const timers: number[] = [];
  stage.onArrive((id) => {
    if (id !== "form" || toured) return;
    toured = true;
    labels.forEach((_, i) => {
      timers.push(
        window.setTimeout(
          () => {
            light(i, true);
            show(i, 0.55);
          },
          700 + i * 620,
        ),
        window.setTimeout(() => light(i, false), 700 + i * 620 + 900),
      );
    });
  });
  const enter = (i: number) => () => {
    light(i, true);
    show(i);
  };
  const leave = (i: number) => () => light(i, false);
  const handlers = labels.map((l, i) => {
    const a = enter(i);
    const b = leave(i);
    l.addEventListener("pointerenter", a);
    l.addEventListener("pointerleave", b);
    return () => {
      l.removeEventListener("pointerenter", a);
      l.removeEventListener("pointerleave", b);
    };
  });
  return () => {
    timers.forEach(clearTimeout);
    handlers.forEach((h) => h());
  };
}

// What each thing she won't do gets from her (Manners.astro, in order).
const MANNER_CUES: Cue[] = ["open", "hush", "unimpressed", "avoid"];

/** What she won't do: she listens with her eyes closed; pointing at each
    line gets her answer to it (the second one, talking over the room, gets
    nothing, which is the answer). */
function manners(stage: Stage): () => void {
  const items = [...document.querySelectorAll<HTMLElement>("#manners .list li")];
  const handlers = items.map((li, i) => {
    let last = 0;
    const on = () => {
      const t = now();
      if (stage.slot?.id !== "manners" || t - last < 1.2) return;
      last = t;
      stage.brain.cue(MANNER_CUES[i] ?? "hush", t);
      stage.wake();
    };
    li.addEventListener("pointerenter", on);
    return () => li.removeEventListener("pointerenter", on);
  });
  return () => handlers.forEach((h) => h());
}

/** Her notebook (scripts/memory.ts): she writes each line down, then glances at it. */
function notebook(stage: Stage): () => void {
  const timers: number[] = [];
  const on = (e: Event) => {
    const d = (e as CustomEvent<{ el: HTMLElement; dur: number }>).detail;
    if (stage.slot?.id !== "memory") return;
    stage.brain.write(now(), d.dur);
    stage.wake();
    timers.push(
      window.setTimeout(() => {
        if (stage.slot?.id !== "memory") return;
        const c = centre(d.el);
        stage.brain.attend(c.x, c.y, 0.7, now());
        stage.wake();
      }, d.dur * 1000),
    );
  };
  document.addEventListener("nora:writing", on);
  return () => {
    document.removeEventListener("nora:writing", on);
    timers.forEach(clearTimeout);
  };
}
