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
  try {
    stage = new Stage(rigData as unknown as Record<string, PoseRig>, data.urls, fail);
    await stage.start();
    root.dataset.nora = "live";
    disconnect = connect(stage, data.presence);
  } catch (err) {
    console.warn("nora: staying still", err);
    fail();
  }
}

reduce.addEventListener("change", () => (reduce.matches ? fail() : void boot()));
if (document.readyState === "complete") void boot();
else addEventListener("load", () => void boot(), { once: true });

export {};
