// Sound, off until someone asks for it.
//
// The header's earphones button is the only way in: put yours in, and you
// hear what she hears, very quietly. Two clicks as the buds go in, left then
// right; a tick when you send something in the chat demo and a soft pop
// when she answers; the lamp's switch when the lighting changes. All of it
// is synthesised here, from nothing, at a few hundredths of full volume.
// The choice is remembered per browser.
//
// Her voice (the Senses section's "Hear her") is different: it plays when
// its own button is pressed, sound on or off, because pressing it is the
// asking. While it plays, the loudness of her voice is handed to anything
// that wants to move her mouth.
import type { RoomEvent } from "./chat";

type Cue = "in" | "out" | "send" | "receive" | "lamp";

const KEY = "nora-sound";
let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let on = false;
try {
  on = localStorage.getItem(KEY) === "on";
} catch {
  /* no storage: off, and not remembered */
}

function audio(): AudioContext | null {
  if (ctx) return ctx;
  const Ctx =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) return null;
  ctx = new Ctx();
  master = ctx.createGain();
  master.gain.value = 0.9;
  master.connect(ctx.destination);
  return ctx;
}

/** A soft click: a breath of filtered noise and a low knock, panned. */
function click(at: number, pan: number, gain: number): void {
  const c = ctx!;
  const len = Math.round(c.sampleRate * 0.03);
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
  const noise = c.createBufferSource();
  noise.buffer = buf;
  const bp = c.createBiquadFilter();
  bp.type = "bandpass";
  bp.frequency.value = 2600;
  bp.Q.value = 0.9;
  const g = c.createGain();
  g.gain.value = gain;
  const p = c.createStereoPanner();
  p.pan.value = pan;
  noise.connect(bp).connect(g).connect(p).connect(master!);
  noise.start(at);
  const knock = c.createOscillator();
  knock.frequency.setValueAtTime(170, at);
  knock.frequency.exponentialRampToValueAtTime(90, at + 0.05);
  const kg = c.createGain();
  kg.gain.setValueAtTime(gain * 0.9, at);
  kg.gain.exponentialRampToValueAtTime(0.0001, at + 0.07);
  knock.connect(kg).connect(p);
  knock.start(at);
  knock.stop(at + 0.08);
}

/** A soft sine note with a quick fall, like a marimba played very quietly. */
function note(at: number, freq: number, gain: number, dur = 0.16, type: OscillatorType = "sine"): void {
  const c = ctx!;
  const o = c.createOscillator();
  o.type = type;
  o.frequency.setValueAtTime(freq, at);
  o.frequency.exponentialRampToValueAtTime(freq * 0.985, at + dur);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(gain, at + 0.008);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  o.connect(g).connect(master!);
  o.start(at);
  o.stop(at + dur + 0.02);
}

export function play(cue: Cue): void {
  if (!on && cue !== "in" && cue !== "out") return;
  const c = audio();
  if (!c || !master) return;
  if (c.state === "suspended") void c.resume();
  const t = c.currentTime + 0.01;
  switch (cue) {
    case "in":
      click(t, -0.75, 0.05);
      click(t + 0.13, 0.75, 0.05);
      break;
    case "out":
      click(t, 0.75, 0.03);
      click(t + 0.1, -0.75, 0.03);
      break;
    case "send":
      note(t, 1318, 0.018, 0.07);
      break;
    case "receive":
      note(t, 659, 0.03, 0.18, "triangle");
      note(t + 0.075, 988, 0.022, 0.22, "triangle");
      break;
    case "lamp":
      click(t, 0.2, 0.045);
      break;
  }
}

export function soundOn(): boolean {
  return on;
}

// ------------------------------------------------------------ the button

const button = document.querySelector<HTMLButtonElement>("[data-sound-toggle]");
function sync(): void {
  if (!button) return;
  button.setAttribute("aria-pressed", String(on));
  const label = on ? button.dataset.labelOn : button.dataset.labelOff;
  if (label) button.setAttribute("aria-label", label);
}
if (button) {
  button.hidden = false;
  sync();
  button.addEventListener("click", () => {
    on = !on;
    try {
      localStorage.setItem(KEY, on ? "on" : "off");
    } catch {
      /* the choice lasts for this page only */
    }
    sync();
    play(on ? "in" : "out");
    document.dispatchEvent(new CustomEvent("nora:earphones", { detail: { on } }));
  });
}

// the chat demo and the lighting, heard
document.addEventListener("nora:room", (e) => {
  const d = (e as CustomEvent<RoomEvent>).detail;
  if (d.kind === "ask") play("send");
  if (d.kind === "reply") play("receive");
});
new MutationObserver(() => play("lamp")).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ["data-theme"],
});

// -------------------------------------------------------------- her voice

/** Loudness of her voice while a clip plays, 0..1, read by her mouth. */
export let speaking = 0;

function voice(root: HTMLElement): void {
  const btn = root.querySelector<HTMLButtonElement>("[data-voice-play]");
  const src = root.dataset.voiceSrc;
  if (!btn || !src) return;
  root.hidden = false;
  let el: HTMLAudioElement | null = null;
  let analyser: AnalyserNode | null = null;
  let raf = 0;
  const levels = new Uint8Array(256);
  const stop = () => {
    el?.pause();
    if (el) el.currentTime = 0;
    root.classList.remove("playing");
    btn.setAttribute("aria-pressed", "false");
    cancelAnimationFrame(raf);
    speaking = 0;
    document.dispatchEvent(new CustomEvent("nora:voice", { detail: { level: 0 } }));
  };
  const meter = () => {
    if (analyser) {
      analyser.getByteTimeDomainData(levels);
      let sum = 0;
      for (const v of levels) sum += ((v - 128) / 128) ** 2;
      const rms = Math.sqrt(sum / levels.length);
      speaking = Math.min(1, rms * 5.5);
      document.dispatchEvent(new CustomEvent("nora:voice", { detail: { level: speaking } }));
    }
    raf = requestAnimationFrame(meter);
  };
  btn.addEventListener("click", async () => {
    if (root.classList.contains("playing")) return stop();
    if (!el) {
      el = new Audio(src);
      el.preload = "auto";
      el.addEventListener("ended", stop);
      const c = audio();
      if (c && master) {
        try {
          const node = c.createMediaElementSource(el);
          analyser = c.createAnalyser();
          analyser.fftSize = 512;
          node.connect(analyser);
          node.connect(c.destination);
        } catch {
          analyser = null;
        }
      }
    }
    if (ctx?.state === "suspended") await ctx.resume();
    root.classList.add("playing");
    btn.setAttribute("aria-pressed", "true");
    try {
      await el.play();
      meter();
    } catch {
      stop();
    }
  });
}

const voiceRoot = document.querySelector<HTMLElement>("[data-voice]");
if (voiceRoot) voice(voiceRoot);
