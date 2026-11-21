// The story's timeline over the whole page's scroll (DESIGN.md §9). Each
// scene owns a span of progress and a station; between stations the camera
// flies through a bank (warm cloud or cool fog), except where the story
// cuts differently: the rewind runs the scenes backwards, and the path goes
// from blue hour into night without leaving.
export type SceneId =
  | "hero" | "circles" | "memory" | "knowledge" | "rewind" | "nora"
  | "shift" | "act" | "rooms" | "soulbound" | "gate";

export interface Scene {
  id: SceneId;
  station: string;
  a: number;
  b: number;
  /** How the camera arrives from the scene before. */
  enter: "cloud" | "fog" | "flash" | "dusk" | "none";
}

export const SCENES: Scene[] = [
  { id: "hero", station: "hero", a: 0, b: 0.085, enter: "cloud" },
  { id: "circles", station: "circles", a: 0.085, b: 0.17, enter: "cloud" },
  { id: "memory", station: "memory", a: 0.17, b: 0.255, enter: "fog" },
  { id: "knowledge", station: "ridge", a: 0.255, b: 0.345, enter: "fog" },
  { id: "rewind", station: "ridge", a: 0.345, b: 0.4, enter: "none" },
  { id: "nora", station: "path", a: 0.4, b: 0.515, enter: "flash" },
  { id: "shift", station: "path", a: 0.515, b: 0.605, enter: "dusk" },
  { id: "act", station: "terrace", a: 0.605, b: 0.725, enter: "cloud" },
  { id: "rooms", station: "circles", a: 0.725, b: 0.8, enter: "cloud" },
  { id: "soulbound", station: "path", a: 0.8, b: 0.895, enter: "fog" },
  { id: "gate", station: "gate", a: 0.895, b: 1, enter: "cloud" },
];

/** Half the width of a bank, in page progress. */
export const BANK = 0.016;

export const clamp = (x: number): number => Math.min(1, Math.max(0, x));
export const ramp = (x: number, a: number, b: number): number => clamp((x - a) / (b - a));
export const ease = (x: number): number => x * x * (3 - 2 * x);
export const lerp = (a: number, b: number, k: number): number => a + (b - a) * k;

export function sceneAt(p: number): { scene: Scene; l: number; index: number } {
  let index = SCENES.findIndex((s) => p < s.b);
  if (index < 0) index = SCENES.length - 1;
  const scene = SCENES[index];
  return { scene, l: clamp((p - scene.a) / (scene.b - scene.a)), index };
}

/** Where the camera is in a bank: 0..1 through it, or -1 outside any. */
export function bank(p: number): { k: number; warm: boolean } {
  for (const s of SCENES) {
    if (s.enter !== "cloud" && s.enter !== "fog") continue;
    if (s.a === 0) continue;
    if (Math.abs(p - s.a) < BANK) return { k: (p - (s.a - BANK)) / (2 * BANK), warm: s.enter === "cloud" };
  }
  return { k: -1, warm: true };
}
