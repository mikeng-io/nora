// Her notebook: under each of the five memory tiers, the line she would be
// keeping from the chat demo above it. Playing the demo rewrites the fast
// three (the words as they were said; where the room stands, rewritten after
// every exchange; what is still open, with the reminder retired once it has
// been sent) and leaves the slow two alone, because a day's culture and a
// person's portrait do not change in one evening. Five speeds, shown.
//
// Changes made while the section is off screen wait, and are written down
// one at a time when it comes into view, which is when she is on her stool
// to write them (scripts/nora/wire.ts listens for `nora:writing`). Without
// motion they simply appear. Without script the page shows the room's
// state before anything was asked.
import type { RoomEvent } from "./chat";

interface Kept {
  standing: Record<string, string>;
  open: { table: string; reminder: string; retired: string };
}

/** What a fast tier says: plain words, or (what's still open) a short list. */
type Line = { words: string } | { items: { text: string; retired?: boolean }[] };

const scale = document.querySelector<HTMLElement>("#memory [data-kept]");

function init(scale: HTMLElement): void {
  const kept = JSON.parse(scale.dataset.kept ?? "{}") as Kept;
  const line = (i: number) => scale.querySelector<HTMLElement>(`[data-kept-tier="${i}"]`);
  const text = (i: number) => line(i)?.querySelector<HTMLElement>("[data-kept-text]") ?? null;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");

  // what each fast tier said before anything was asked, for "Start over"
  const seed = [0, 1, 2].map((i) => [...(text(i)?.childNodes ?? [])].map((n) => n.cloneNode(true)));
  // what each fast tier should say now; `null` = nothing new since it was shown
  const want: (Line | "seed" | null)[] = [null, null, null];
  const state = { reminder: false, retired: false };
  let visible = false;
  let writing = false;

  const said = (li: HTMLElement): Line => {
    const name = li.querySelector(".name")?.textContent?.trim() ?? "";
    const time = li.querySelector("time")?.textContent?.trim() ?? "";
    const words = li.querySelector(".text")?.textContent?.trim() ?? "";
    return { words: `${time} · ${name}: ${words}` };
  };
  const open = (): Line => {
    const items: { text: string; retired?: boolean }[] = [{ text: kept.open.table }];
    if (state.retired) items.push({ text: kept.open.retired, retired: true });
    else if (state.reminder) items.push({ text: kept.open.reminder });
    return { items };
  };

  const plain = (l: Line) => ("words" in l ? l.words : l.items.map((i) => i.text).join(" "));
  const render = (el: HTMLElement, l: Line | "seed", i: number) => {
    if (l === "seed") return el.replaceChildren(...seed[i].map((n) => n.cloneNode(true)));
    if ("words" in l) return el.replaceChildren(l.words);
    el.replaceChildren(
      ...l.items.map((it) => {
        const span = document.createElement("span");
        span.className = it.retired ? "item retired" : "item";
        span.textContent = it.text;
        return span;
      }),
    );
  };

  const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  /** Writes tier `i` its new line: letter by letter while she writes, or at once. */
  async function write(i: number, l: Line | "seed"): Promise<void> {
    const el = text(i);
    const host = line(i);
    if (!el || !host) return;
    host.classList.remove("fresh");
    if (!reduce.matches && l !== "seed") {
      document.dispatchEvent(new CustomEvent("nora:writing", { detail: { el: host, dur: 1.1 } }));
      const chars = [...plain(l)];
      const step = Math.max(12, Math.min(28, 900 / Math.max(chars.length, 1)));
      for (let n = 1; n <= chars.length; n++) {
        el.textContent = chars.slice(0, n).join("");
        await wait(step);
      }
    }
    render(el, l, i);
    if (l === "seed") return;
    void host.offsetWidth;
    host.classList.add("fresh");
  }

  async function flush(): Promise<void> {
    if (writing || !visible) return;
    writing = true;
    for (let i = 0; i < 3; i++) {
      const l = want[i];
      if (l === null) continue;
      want[i] = null;
      await write(i, l);
      if (!reduce.matches && l !== "seed") await wait(260);
    }
    writing = false;
    if (want.some((w) => w !== null)) void flush();
  }

  document.addEventListener("nora:room", (e) => {
    const d = (e as CustomEvent<RoomEvent>).detail;
    if (d.kind === "reset") {
      state.reminder = false;
      state.retired = false;
      want.fill("seed");
    } else if (d.kind === "ask" && d.el) {
      want[0] = said(d.el);
    } else if (d.kind === "reply" && d.el && d.id) {
      want[0] = said(d.el);
      if (kept.standing[d.id]) want[1] = { words: kept.standing[d.id] };
      if (d.id === "remind") state.reminder = true;
      if (d.id === "later") state.retired = true;
      if (d.id === "remind" || d.id === "later") want[2] = open();
    } else {
      return;
    }
    void flush();
  });

  new IntersectionObserver(
    (entries) => {
      visible = entries.some((e) => e.isIntersecting);
      // give her a moment to get to her stool before she writes
      if (visible) setTimeout(() => void flush(), reduce.matches ? 0 : 900);
    },
    { threshold: 0.15 },
  ).observe(scale);
}

if (scale) init(scale);
