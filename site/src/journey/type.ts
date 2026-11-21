// The story's words, choreographed by scroll (DESIGN.md §4 the Real-Text
// Rule, §6). Every title stays real text: it is split into letters only for
// motion, with the whole sentence kept as its accessible name. Each beat's
// title moves the way its words mean:
//   rise   — arrives letter by letter, lifting into place
//   drop   — falls and settles, a little crooked, like slips onto a pile
//   ignite — catches copper light as it lands, then settles into ink
//   crop   — each line slides in huge from the side, one after another
type Style = "rise" | "drop" | "ignite" | "crop";

interface Beat {
  el: HTMLElement;
  style: Style;
  chars: HTMLElement[];
  lines: HTMLElement[];
  rest: HTMLElement[];
  seed: number[];
}

const clamp = (x: number) => Math.min(1, Math.max(0, x));
const ramp = (x: number, a: number, b: number) => clamp((x - a) / (b - a));
const ease = (x: number) => x * x * (3 - 2 * x);

function split(h: HTMLElement): HTMLElement[] {
  h.setAttribute("aria-label", h.textContent?.trim() ?? "");
  const chars: HTMLElement[] = [];
  const walk = (node: Node, host: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      for (const word of (node.textContent ?? "").split(/(\s+)/)) {
        if (!word) continue;
        if (/^\s+$/.test(word)) {
          host.appendChild(document.createTextNode(" "));
          continue;
        }
        const w = document.createElement("span");
        w.className = "w";
        w.setAttribute("aria-hidden", "true");
        for (const ch of word) {
          const c = document.createElement("span");
          c.className = "c";
          c.textContent = ch;
          w.appendChild(c);
          chars.push(c);
        }
        host.appendChild(w);
      }
    } else {
      const clone = node.cloneNode(false);
      for (const child of [...node.childNodes]) walk(child, clone);
      host.appendChild(clone);
    }
  };
  const frag = document.createDocumentFragment();
  for (const child of [...h.childNodes]) walk(child, frag);
  h.replaceChildren(frag);
  return chars;
}

export class Type {
  private readonly beats = new Map<string, Beat>();
  private readonly originals: [HTMLElement, string][] = [];

  constructor(root: HTMLElement) {
    for (const el of root.querySelectorAll<HTMLElement>("[data-beat]")) {
      const style = (el.dataset.style ?? "rise") as Style;
      const lines = [...el.querySelectorAll<HTMLElement>("[data-line]")];
      const titles = lines.length ? lines : [...el.querySelectorAll<HTMLElement>("[data-split]")];
      for (const t of titles) this.originals.push([t, t.innerHTML]);
      const chars = titles.flatMap(split);
      this.beats.set(el.dataset.beat!, {
        el,
        style,
        chars,
        lines,
        rest: [...el.querySelectorAll<HTMLElement>("[data-rest]")],
        seed: chars.map((_, i) => Math.sin(i * 12.9898 + 4.1) % 1),
      });
    }
  }

  /** Show one beat at local progress l (0..1 across its scene); hide when outside. */
  show(id: string, l: number): void {
    const b = this.beats.get(id);
    if (!b) return;
    const on = l > -0.01 && l < 1.01;
    b.el.classList.toggle("on", on);
    if (!on) return;
    const n = b.chars.length || 1;
    b.chars.forEach((c, i) => this.letter(b, c, i, n, l));
    const r = ease(ramp(l, 0.14, 0.32)) * (1 - ease(ramp(l, 0.8, 0.94)));
    for (const e of b.rest) {
      e.style.opacity = String(r);
      e.style.transform = `translate3d(0, ${((1 - r) * 16).toFixed(1)}px, 0)`;
    }
  }

  /** Put every title and its words back as they were written: the static story. */
  restore(): void {
    for (const [el, html] of this.originals) {
      el.innerHTML = html;
      el.removeAttribute("aria-label");
    }
    for (const b of this.beats.values()) {
      b.el.classList.remove("on");
      for (const e of b.rest) e.removeAttribute("style");
    }
  }

  hideAll(except: Set<string>): void {
    for (const [id, b] of this.beats) if (!except.has(id)) b.el.classList.remove("on");
  }

  private letter(b: Beat, c: HTMLElement, i: number, n: number, l: number): void {
    const line = b.lines.length ? b.lines.findIndex((x) => x.contains(c)) : 0;
    const start = b.style === "crop" ? line * 0.16 : 0;
    const a = ease(clamp(ramp(l, start, start + 0.22) * 1.7 - (i / n) * 0.7));
    const o = ease(clamp(ramp(l, 0.82, 1) * 1.7 - (i / n) * 0.7));
    const s = b.seed[i];
    let tf = "";
    let filter = a < 0.99 || o > 0.01 ? `blur(${((1 - a) * 6 + o * 8).toFixed(1)}px)` : "none";
    switch (b.style) {
      case "drop":
        tf = `translate3d(0, ${(-(1 - a) * 1.4 + o * 0.5).toFixed(3)}em, 0) rotate(${((1 - a) * 18 * s + s * 2.2).toFixed(2)}deg)`;
        filter = "none";
        break;
      case "crop":
        tf = `translate3d(${((1 - a) * (line % 2 ? -40 : 40)).toFixed(2)}vw, 0, 0)`;
        filter = "none";
        break;
      case "ignite": {
        const glow = Math.max(0, 1 - Math.abs(a - 0.7) * 3);
        c.style.textShadow = glow > 0.01 ? `0 0 ${(24 * glow).toFixed(1)}px rgba(248,178,132,${(0.9 * glow).toFixed(2)})` : "";
        c.style.color = glow > 0.3 ? "var(--accent-text)" : "";
        tf = `translate3d(0, ${((1 - a) * 0.3 - o * 0.5).toFixed(3)}em, 0)`;
        break;
      }
      default:
        tf = `translate3d(0, ${((1 - a) * 0.55 - o * 0.6).toFixed(3)}em, 0) rotate(${((1 - a) * 7 - o * 4).toFixed(2)}deg)`;
    }
    c.style.opacity = String(a * (1 - o));
    c.style.transform = tf;
    c.style.filter = filter;
  }
}
