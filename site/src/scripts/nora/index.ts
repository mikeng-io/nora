// Starts her, if this browser should have her, and connects her to the page.
//
// She is progressive like everything else on the site: no script, reduced
// motion, a data saver, no WebGL2, a lost GPU context, or any error at all,
// and the page keeps the static renders it was built with. She starts after
// the page has loaded, so she never competes with the hero's first paint.
import rigData from "../../assets/rig/rig.json";
import { Stage } from "./stage";
import { connect } from "./wire";
import type { PoseRig } from "./types";

interface Data {
  urls: Record<string, string>;
  presence: Record<string, string>;
}

const reduce = matchMedia("(prefers-reduced-motion: reduce)");
const root = document.documentElement;
let stage: Stage | null = null;
let disconnect: (() => void) | null = null;

function fail(): void {
  disconnect?.();
  disconnect = null;
  stage?.destroy();
  stage = null;
  delete root.dataset.nora;
}

async function boot(): Promise<void> {
  if (stage || reduce.matches) return;
  const conn = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
  if (conn?.saveData) return;
  const el = document.getElementById("nora-rig");
  if (!el?.textContent) return;
  const data = JSON.parse(el.textContent) as Data;
  // `mine` is this boot's stage: anything that stops it (reduced motion
  // switched on, a lost GPU context) while it is still starting makes it
  // stale, and a stale boot must not connect, or undo what `fail()` restored.
  let mine: Stage | null = null;
  try {
    mine = new Stage(rigData as unknown as Record<string, PoseRig>, data.urls, () => {
      if (stage === mine) fail();
    });
    stage = mine;
    await mine.start();
    if (stage !== mine) return;
    root.dataset.nora = "live";
    disconnect = connect(mine, data.presence);
  } catch (err) {
    if (stage !== mine) return;
    console.warn("nora: staying still", err);
    fail();
  }
}

reduce.addEventListener("change", () => (reduce.matches ? fail() : void boot()));
if (document.readyState === "complete") void boot();
else addEventListener("load", () => void boot(), { once: true });

export {};
