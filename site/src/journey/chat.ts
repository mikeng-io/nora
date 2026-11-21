// The room in the act scene: a scripted group chat (it says so) that plays
// as you scroll, two questions you can ask her, and her own message the next
// morning. "How she knew" draws a copper thread from her answer to the very
// lines it rests on (DESIGN.md §9; design/cognition: claims cite what was seen).
// Without script the whole transcript is already on the page.
//
// The chat follows the scroll both ways: scrolling back past the questions
// takes back what was asked, so the scene replays cleanly.
export class Chat {
  private readonly root: HTMLElement | null;
  private readonly seed: HTMLElement[];
  private readonly own: HTMLElement | null;
  private readonly svg: SVGSVGElement | null;
  private timers: number[] = [];
  private asked = false;
  onEvent: (kind: "ask" | "reply" | "own") => void = () => {};

  constructor(private readonly signal: AbortSignal) {
    this.root = document.querySelector("[data-chat]");
    this.seed = [...document.querySelectorAll<HTMLElement>("[data-chat-line]")];
    this.own = document.querySelector("[data-chat-own]");
    this.svg = document.querySelector("[data-chat-threads]");
    if (!this.root) return;
    this.root.classList.add("live");
    for (const b of this.root.querySelectorAll<HTMLButtonElement>("[data-ask]")) b.addEventListener("click", () => this.ask(b), { signal: this.signal });
    for (const r of this.root.querySelectorAll<HTMLElement>("[data-reply]")) {
      r.querySelector("[data-how]")?.addEventListener("click", () => this.how(r), { signal: this.signal });
    }
  }

  /** Lines arrive with scroll; her own message comes the next morning. */
  frame(active: boolean, l: number): void {
    if (!this.root) return;
    if ((!active || l < 0.3) && this.asked) this.reset();
    this.seed.forEach((el, i) => el.classList.toggle("in", active && l > 0.08 + i * 0.045));
    this.root.querySelector("[data-asks]")?.classList.toggle("in", active && l > 0.3);
    const own = active && l > 0.68;
    if (this.own && own !== this.own.classList.contains("in")) {
      this.own.classList.toggle("in", own);
      this.root.querySelector("[data-morning]")?.classList.toggle("in", own);
      if (own) this.onEvent("own");
    }
    const log = this.root.querySelector<HTMLElement>(".log");
    if (log && active) log.scrollTop = log.scrollHeight;
    if (!active) this.clear();
  }

  private ask(b: HTMLButtonElement): void {
    if (b.disabled || !this.root) return;
    for (const x of this.root.querySelectorAll<HTMLButtonElement>("[data-ask]")) x.disabled = true;
    this.asked = true;
    const id = b.dataset.ask!;
    this.root.querySelector(`[data-you="${id}"]`)?.classList.add("in");
    this.onEvent("ask");
    const typing = this.root.querySelector("[data-typing]");
    typing?.classList.add("in");
    this.timers.push(
      window.setTimeout(() => {
        typing?.classList.remove("in");
        const reply = this.root?.querySelector<HTMLElement>(`[data-reply="${id}"]`);
        reply?.classList.add("in");
        this.onEvent("reply");
        if (reply) this.timers.push(window.setTimeout(() => this.how(reply), 600));
        // the other question stays open
        for (const x of this.root?.querySelectorAll<HTMLButtonElement>("[data-ask]") ?? []) {
          if (!this.root?.querySelector(`[data-reply="${x.dataset.ask}"].in`)) x.disabled = false;
        }
      }, 1100),
    );
  }

  /** Draw her answer's threads back to the lines it rests on. Can be opened again. */
  private how(reply: HTMLElement): void {
    if (!this.svg || !this.root) return;
    this.clear();
    const box = this.root.getBoundingClientRect();
    const from = reply.getBoundingClientRect();
    const cites = (reply.dataset.cites ?? "").split(" ").filter(Boolean);
    this.svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    for (const id of cites) {
      const line = this.root.querySelector<HTMLElement>(`[data-chat-line="${id}"]`);
      if (!line) continue;
      line.classList.add("cited");
      const to = line.getBoundingClientRect();
      const x1 = from.left - box.left + 6, y1 = from.top - box.top + from.height / 2;
      const x2 = to.left - box.left + 6, y2 = to.top - box.top + to.height / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const bend = Math.min(70, Math.abs(y1 - y2) * 0.35 + 24);
      path.setAttribute("d", `M ${x1} ${y1} C ${x1 - bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
      this.svg.appendChild(path);
    }
    reply.classList.add("shown");
  }

  /** Take back what was asked: questions open again, answers and threads gone. */
  private reset(): void {
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
    this.asked = false;
    if (!this.root) return;
    for (const el of this.root.querySelectorAll("[data-you], [data-reply], [data-typing]")) el.classList.remove("in", "shown");
    for (const x of this.root.querySelectorAll<HTMLButtonElement>("[data-ask]")) x.disabled = false;
    this.clear();
  }

  /** Back to the written transcript: everything shown, nothing live. */
  detach(): void {
    for (const t of this.timers) clearTimeout(t);
    this.root?.classList.remove("live");
    for (const el of this.root?.querySelectorAll(".in, .shown, .cited") ?? []) el.classList.remove("in", "shown", "cited");
    this.svg?.replaceChildren();
  }

  private clear(): void {
    this.svg?.replaceChildren();
    for (const el of this.seed) el.classList.remove("cited");
  }
}
