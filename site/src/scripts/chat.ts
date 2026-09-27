// The chat demo. Without this script the page shows the whole scripted
// conversation as a static transcript; with it, the conversation starts at
// the group's own messages and each "Ask her" button plays one exchange.
//
// Motion follows DESIGN.md §6: a message enters in 240ms (opacity and a 6px
// rise, opacity only under reduced motion), and her thinking dots are the
// one loop, running only while she "thinks".

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const reduce = matchMedia("(prefers-reduced-motion: reduce)");

/** What happened in the room, for whoever is listening: her figure on the
    window (scripts/nora/wire.ts), her notebook (scripts/memory.ts), the sound. */
export interface RoomEvent {
  kind: "ask" | "think" | "reply" | "skin" | "reset";
  id?: string;
  el?: HTMLElement;
  own?: boolean;
  thinking?: boolean;
}

function tell(detail: RoomEvent): void {
  document.dispatchEvent(new CustomEvent<RoomEvent>("nora:room", { detail }));
}

function clock(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function init(root: HTMLElement): void {
  const log = root.querySelector<HTMLOListElement>("[data-log]");
  const typing = root.querySelector<HTMLLIElement>("[data-typing]");
  const controls = root.querySelector<HTMLElement>("[data-chat-controls]");
  const replay = root.querySelector<HTMLButtonElement>("[data-replay]");
  const windowEl = root.querySelector<HTMLElement>(".window");
  if (!log || !typing || !controls || !replay || !windowEl) return;

  const prompts = [...root.querySelectorAll<HTMLButtonElement>("[data-prompt]")];
  const used = new Set<string>();
  let busy = false;
  // Bumped by every reset: an exchange still playing when "Start over" is
  // pressed sees a different generation after its next wait and stops,
  // instead of finishing into the fresh thread.
  let generation = 0;
  // The room's clock: the thread resumes at 21:40 on the night of the plan.
  let now = 21 * 60 + 40;

  const stepItems = (id: string) => [...log.querySelectorAll<HTMLLIElement>(`li[data-step="${id}"]`)];

  function refresh(): void {
    for (const b of prompts) {
      const id = b.dataset.prompt ?? "";
      const after = b.dataset.after;
      const done = used.has(id);
      b.classList.toggle("used", done);
      // aria-disabled, not disabled: a used prompt keeps its place in the tab
      // order, so focus never falls back to the top of the page.
      b.setAttribute("aria-disabled", String(busy || done || (after !== undefined && !used.has(after))));
    }
    replay!.hidden = used.size === 0;
  }

  function toEnd(): void {
    log!.scrollTo({ top: log!.scrollHeight, behavior: reduce.matches ? "auto" : "smooth" });
  }

  function reveal(item: HTMLElement): void {
    log!.insertBefore(item, typing);
    item.hidden = false;
    item.classList.remove("enter");
    void item.offsetWidth;
    item.classList.add("enter");
    toEnd();
  }

  function reset(): void {
    generation += 1;
    used.clear();
    now = 21 * 60 + 40;
    for (const li of log!.querySelectorAll<HTMLLIElement>("li[data-step]")) {
      li.hidden = true;
      li.classList.remove("enter");
    }
    typing!.hidden = true;
    busy = false;
    refresh();
    log!.scrollTo({ top: log!.scrollHeight });
    tell({ kind: "reset" });
  }

  async function play(button: HTMLButtonElement): Promise<void> {
    const id = button.dataset.prompt ?? "";
    if (busy || button.getAttribute("aria-disabled") === "true") return;
    const mine = generation;
    busy = true;
    refresh();
    const [first, reply] = stepItems(id);
    const skip = first.classList.contains("divider");
    if (skip) {
      // Skipping ahead: the clock moves to Saturday evening.
      now = 18 * 60;
    } else {
      now += 1 + Math.floor(Math.random() * 2);
      for (const t of [first, reply]) {
        const time = t.querySelector<HTMLElement>("[data-clock]");
        if (time) time.textContent = clock(now);
      }
    }
    reveal(first);
    if (!skip) tell({ kind: "ask", id, el: first });
    await wait(skip ? 700 : 450);
    if (mine !== generation) return;
    if (!skip) {
      typing!.hidden = false;
      typing!.classList.remove("enter");
      void typing!.offsetWidth;
      typing!.classList.add("enter");
      toEnd();
      tell({ kind: "think", id, el: typing!, thinking: true });
      await wait(reduce.matches ? 700 : 1200);
      if (mine !== generation) return;
      typing!.hidden = true;
      tell({ kind: "think", id, thinking: false });
    }
    reveal(reply);
    tell({ kind: "reply", id, el: reply, own: skip });
    used.add(id);
    busy = false;
    refresh();
  }

  for (const b of prompts) b.addEventListener("click", () => void play(b));
  replay.addEventListener("click", reset);

  // One DOM, four skins: switching platform restyles the same messages.
  const skins = [...root.querySelectorAll<HTMLButtonElement>("[data-platform]")];
  const labels = [...root.querySelectorAll<HTMLElement>("[data-on]")];
  for (const b of skins) {
    b.addEventListener("click", async () => {
      const skin = b.dataset.platform ?? "signal";
      if (windowEl.dataset.skin === skin) return;
      for (const s of skins) s.setAttribute("aria-pressed", String(s === b));
      log.classList.add("switching");
      await wait(reduce.matches ? 0 : 100);
      windowEl.dataset.skin = skin;
      for (const l of labels) l.hidden = l.dataset.on !== skin;
      log.classList.remove("switching");
      log.scrollTo({ top: log.scrollHeight });
      tell({ kind: "skin", id: skin });
    });
  }

  controls.hidden = false;
  reset();
}

const root = document.querySelector<HTMLElement>("[data-chat]");
if (root) init(root);
