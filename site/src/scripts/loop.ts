// "Two ways of being there", walked.
//
// When you call her: the first time the straight cable comes into view, a
// signal runs down it, one stop at a time, at about the pace of a real turn
// scaled down, ending on her reply in blush. Pointing at it runs it again.
//
// When nobody has: the loop underneath everything is live state (it runs
// all day on her host), so while it is on screen it is walked, slowly,
// stop by stop, around and around. It passes "Act" almost every time without
// lighting it: she almost never speaks unasked. About one lap in six, it lights.
//
// Reduced motion: neither runs; the diagram is the diagram.
const reduce = matchMedia("(prefers-reduced-motion: reduce)");
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function signal(list: HTMLElement): void {
  const stops = [...list.querySelectorAll<HTMLLIElement>(":scope > li")];
  let running = false;
  // someone tags her · she reads the room · she thinks, and uses tools · she replies
  const pace = [420, 560, 900, 0];
  async function run(): Promise<void> {
    if (running || reduce.matches) return;
    running = true;
    for (let i = 0; i < stops.length; i++) {
      stops[i].classList.add("on");
      if (i < stops.length - 1) {
        await wait(pace[i] * 0.45);
        stops[i].classList.add("run");
        await wait(pace[i] * 0.55);
      }
    }
    await wait(2600);
    for (const s of stops) s.classList.remove("on", "run");
    await wait(400);
    running = false;
  }
  let seen = false;
  new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting) && !seen) {
        seen = true;
        setTimeout(() => void run(), 350);
      }
    },
    { threshold: 0.6 },
  ).observe(list);
  list.closest("article")?.addEventListener("pointerenter", () => void run());
}

function heartbeat(list: HTMLElement): void {
  const stops = [...list.querySelectorAll<HTMLLIElement>(":scope > li")];
  const act = stops.findIndex((s) => s.classList.contains("her"));
  let timer = 0;
  let at = -1;
  let lap = 0;
  const step = () => {
    stops[at]?.classList.remove("on");
    at = (at + 1) % stops.length;
    if (at === 0) lap++;
    // she almost never speaks: "Act" is passed without lighting, most laps
    const speaks = lap % 6 === 5;
    if (at === act && !speaks) {
      timer = window.setTimeout(step, 520);
      return;
    }
    stops[at].classList.add("on");
    timer = window.setTimeout(step, at === act ? 1600 : 1250);
  };
  const start = () => {
    if (timer || reduce.matches) return;
    list.classList.add("beating");
    step();
  };
  const stop = () => {
    clearTimeout(timer);
    timer = 0;
    list.classList.remove("beating");
    for (const s of stops) s.classList.remove("on");
    at = -1;
  };
  new IntersectionObserver((entries) => (entries.some((e) => e.isIntersecting) ? start() : stop()), {
    threshold: 0.4,
  }).observe(list);
  reduce.addEventListener("change", () => reduce.matches && stop());
}

const straight = document.querySelector<HTMLElement>("[data-signal]");
const loop = document.querySelector<HTMLElement>("[data-heartbeat]");
if (straight) signal(straight);
if (loop) heartbeat(loop);
