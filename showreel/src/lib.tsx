// The shared vocabulary of every scene: tokens, format, time, easing, fonts.
import React from "react";
import {
  Easing,
  continueRender,
  delayRender,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/fonts";
import timeline from "./data/timeline.json";
import en from "./copy/en.json";
import yue from "./copy/yue.json";
import voEn from "./data/vo-en.json";
import voYue from "./data/vo-yue.json";
import artDims from "./data/art.json";

export type Lang = "en" | "yue";
/** width / height of a processed image in public/art (src/data/art.json). */
export const aspect = (name: string) => {
  const d = (artDims as Record<string, { w: number; h: number }>)[name];
  return d ? d.w / d.h : 1;
};
export const TL = timeline;
export const CUE = timeline.cues;
export const SC = timeline.scenes;
export const FPS = timeline.fps;

// ── tokens: DESIGN.md, both lightings ───────────────────────────────────────
export const NIGHT = {
  bg: "#141112", surface: "#1B1718", hoodie: "#241F20", hairline: "#332C2D", seam: "#4A4243",
  ink: "#F2EDED", inkSoft: "#B9AFAF", inkMuted: "#948989", denim: "#9AA9CA", denimDim: "#7C89A8",
  blush: "#EFA497", blushWash: "#3A2624", bun: "#C99A86", cable: "#F4F0EE",
};
export const STUDIO = {
  bg: "#F4F2F2", surface: "#FCFBFB", hoodie: "#ECE8E8", hairline: "#DCD5D5", seam: "#BDB3B3",
  ink: "#2B2220", inkSoft: "#5E5351", inkMuted: "#6E6362", denim: "#48587A", denimDim: "#5D6C8E",
  blush: "#C26A5A", blushWash: "#F6D9D3", bun: "#5A3D34", cable: "#FFFFFF",
};
export type Theme = typeof NIGHT;

// People keep the portal's stable hue (app/src/lib/person-color.ts).
export const hueOf = (id: string) => {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % 360;
};
export const personColor = (id: string, night = true) =>
  `hsl(${hueOf(id.toLowerCase())} ${night ? 55 : 45}% ${night ? 68 : 42}%)`;

// ── the copy, per language ───────────────────────────────────────────────────
export const COPY = { en, yue };
export const VO = { en: voEn, yue: voYue };
export type Copy = typeof en;

// ── format ───────────────────────────────────────────────────────────────────
export type Kind = "wide" | "tall" | "square" | "feed";
export const useFmt = () => {
  const { width: w, height: h } = useVideoConfig();
  const r = w / h;
  const kind: Kind = r > 1.3 ? "wide" : r < 0.7 ? "tall" : r > 0.95 ? "square" : "feed";
  const u = Math.min(w, h) / 1080;
  return { w, h, u, kind, r };
};
export const pick = <T,>(kind: Kind, v: { wide: T; tall: T; square: T; feed?: T }): T =>
  kind === "feed" ? (v.feed ?? v.square) : v[kind];

// ── time and motion ─────────────────────────────────────────────────────────
export const useT = () => useCurrentFrame() / FPS;
export const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
export const prog = (t: number, a: number, b: number) => clamp01((t - a) / (b - a));
export const easeOut = Easing.bezier(0.2, 0, 0, 1); // DESIGN.md §6, entering
export const easeIn = Easing.bezier(0.4, 0, 1, 1); // DESIGN.md §6, exiting
export const expoOut = (x: number) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x));
export const expoInOut = (x: number) =>
  x <= 0 ? 0 : x >= 1 ? 1 : x < 0.5 ? Math.pow(2, 20 * x - 10) / 2 : (2 - Math.pow(2, -20 * x + 10)) / 2;
export const smooth = (x: number) => x * x * (3 - 2 * x);
export const lerp = (a: number, b: number, x: number) => a + (b - a) * x;
export const springAt = (t: number, start: number, damping = 14, stiffness = 170, mass = 0.8) =>
  spring({ frame: Math.max(0, (t - start) * FPS), fps: FPS, config: { damping, stiffness, mass } });
export const ramp = (t: number, pts: [number, number][]) =>
  interpolate(t, pts.map((p) => p[0]), pts.map((p) => p[1]), {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

// ── fonts ────────────────────────────────────────────────────────────────────
export const FONT = {
  ui: "Inter, 'Noto Sans HK', sans-serif",
  hk: "'Noto Sans HK', Inter, sans-serif",
  word: "'Space Grotesk', Inter, sans-serif",
  mono: "'JetBrains Mono', 'Noto Sans HK', monospace",
};
export const uiFont = (lang: Lang) => (lang === "yue" ? FONT.hk : FONT.ui);

let fontsLoading = false;
export const useFonts = () => {
  if (fontsLoading) return;
  fontsLoading = true;
  const handle = delayRender("fonts");
  Promise.all([
    loadFont({ family: "Inter", url: staticFile("fonts/inter.woff2"), weight: "100 900" }),
    loadFont({ family: "Space Grotesk", url: staticFile("fonts/space-grotesk.woff2"), weight: "300 700" }),
    loadFont({ family: "JetBrains Mono", url: staticFile("fonts/jetbrains-mono.woff2"), weight: "100 800" }),
    loadFont({ family: "Noto Sans HK", url: staticFile("fonts/noto-sans-hk.woff2"), weight: "100 900" }),
  ]).then(() => continueRender(handle));
};

// ── small shared pieces ─────────────────────────────────────────────────────
export const Abs: React.FC<React.PropsWithChildren<{ style?: React.CSSProperties }>> = ({
  children,
  style,
}) => <div style={{ position: "absolute", inset: 0, ...style }}>{children}</div>;

/** Mix two hex colours (for the lights-on crossfade between themes). */
export const mixHex = (a: string, b: string, x: number) => {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const c = pa.map((v, i) => Math.round(lerp(v, pb[i], clamp01(x))));
  return `rgb(${c[0]} ${c[1]} ${c[2]})`;
};

/** Glue the last two words of English copy so a line never ends on one word. */
export const noWidow = (text: string) => text.replace(/ (\S+)$/, "\u00a0$1");
