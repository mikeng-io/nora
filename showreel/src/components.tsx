// Pieces more than one scene uses: grain, headline captions, cut-outs, plates.
import React from "react";
import { Img, staticFile, useCurrentFrame } from "remotion";
import {
  Abs,
  COPY,
  CUE,
  FONT,
  Lang,
  VO,
  clamp01,
  easeOut,
  lerp,
  prog,
  springAt,
  uiFont,
  useFmt,
  useT,
} from "./lib";

/** Film grain and a soft vignette over everything: the "shot on something" layer. */
export const Grain: React.FC<{ strength?: number }> = ({ strength = 0.07 }) => {
  const frame = useCurrentFrame();
  const t = useT();
  // the lights are on from the drop to the collapse: a dark vignette there reads as dirt
  const studio = clamp01(prog(t, 7.95, 8.1) - prog(t, CUE.collapse, CUE.collapse + 0.3));
  const vig = lerp(0.38, 0.1, studio);
  const { w, h } = useFmt();
  const i = Math.floor(frame / 2) % 8;
  const ox = (frame * 137) % 512;
  const oy = (frame * 251) % 512;
  return (
    <>
      <Abs
        style={{
          backgroundImage: `url(${staticFile(`fx/grain-${i}.png`)})`,
          backgroundPosition: `${ox}px ${oy}px`,
          backgroundSize: "512px 512px",
          opacity: strength,
          mixBlendMode: "overlay",
          pointerEvents: "none",
        }}
      />
      <Abs
        style={{
          background: `radial-gradient(ellipse ${w * 0.75}px ${h * 0.75}px at 50% 48%, rgba(0,0,0,0) 55%, rgba(12,8,8,${vig}) 100%)`,
          pointerEvents: "none",
        }}
      />
    </>
  );
};

type Unit = { t: string; at: number; g: number };

/** Split a line's timed tokens into reveal units at absolute times. Cantonese
 *  reveals character by character inside each measured phrase; a Latin word
 *  ("group") stays whole; punctuation rides on the unit before it. `g` groups
 *  units that must not be split across lines: a Cantonese phrase, and the last
 *  two English words (no widows). */
const units = (lang: Lang, id: string, cue: number, size: number, width: number): Unit[] => {
  const line = (VO[lang].lines as Record<string, any>)[id];
  const off = cue - line.speechStart;
  const out: Unit[] = [];
  const timed = line.timed as { t: string; start: number; end: number }[];
  timed.forEach((tok, ti) => {
    if (lang === "en") {
      out.push({ t: tok.t, at: off + tok.start, g: Math.min(ti, timed.length - 2) });
      return;
    }
    // a Latin word keeps the spaces around it; punctuation rides on the unit before
    const parts = tok.t.match(/\s*[A-Za-z0-9]+\s*|[^\sA-Za-z0-9]/g) ?? [];
    const merged: string[] = [];
    for (const p of parts) {
      if (/^[，。？！…、,.?!]$/.test(p) && merged.length) merged[merged.length - 1] += p;
      else if (/^\s/.test(p) && merged.length) {
        // the space before a Latin word ends the previous unit, so a line never starts with it
        merged[merged.length - 1] += " ";
        merged.push(p.trimStart());
      } else merged.push(p);
    }
    // unbroken if the phrase fits the line; otherwise it may wrap, but never
    // leaves its last two units (a character and its full stop) on their own
    const est = merged.reduce((a, m) => a + (/[A-Za-z]/.test(m) ? m.trim().length * 0.56 + 0.5 : 1.04), 0);
    const fits = est * size <= width;
    merged.forEach((m, i) => {
      const g = fits || i >= merged.length - 2 ? ti * 100 : ti * 100 + 1 + i;
      out.push({ t: m, at: off + lerp(tok.start, tok.end, i / Math.max(1, merged.length)), g });
    });
  });
  return out;
};

/** A voice-over line set as display type: each word lands as it is spoken. */
export const Headline: React.FC<{
  lang: Lang;
  id: string;
  x: number;
  y: number;
  width: number;
  size: number;
  color: string;
  align?: "left" | "center" | "right";
  outAt?: number;
  weight?: number;
  soft?: boolean;
  whole?: boolean; // the line lands at once, on its first word
}> = ({ lang, id, x, y, width, size, color, align = "left", outAt, weight = 650, soft, whole }) => {
  const t = useT();
  const line = (VO[lang] as any).lines[id];
  const cue = (COPY[lang].vo as { id: string; cue: number }[]).find((v) => v.id === id)!.cue;
  const us0 = units(lang, id, cue, size, width);
  const us = whole ? us0.map((u) => ({ ...u, at: us0[0].at })) : us0;
  const end = outAt ?? cue - line.speechStart + line.speechEnd + 1.4;
  const out = prog(t, end - 0.25, end);
  if (t < us[0].at - 0.2 || out >= 1) return null;
  const zh = lang === "yue";
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width,
        textAlign: align,
        fontFamily: uiFont(lang),
        fontWeight: weight,
        fontSize: size,
        lineHeight: zh ? 1.22 : 1.04,
        letterSpacing: zh ? "0.02em" : "-0.035em",
        color,
        opacity: 1 - out,
        transform: `translateY(${-out * size * 0.3}px)`,
        filter: out > 0 ? `blur(${out * 6}px)` : undefined,
      }}
    >
      {[...new Set(us.map((u) => u.g))].map((g) => (
        <span key={g} style={{ display: "inline-block", whiteSpace: "nowrap" }}>
          {us.map((u, i) => {
            if (u.g !== g) return null;
            const p = easeOut(prog(t, u.at - 0.07, u.at + (soft ? 0.4 : 0.22)));
            return (
              <span
                key={i}
                style={{
                  display: "inline-block",
                  whiteSpace: "pre",
                  opacity: p,
                  transform: `translateY(${(1 - p) * size * 0.42}px)`,
                  filter: p < 1 ? `blur(${(1 - p) * (soft ? 10 : 7)}px)` : undefined,
                }}
              >
                {u.t}
                {lang === "en" && i < us.length - 1 ? " " : ""}
              </span>
            );
          })}
        </span>
      ))}
    </div>
  );
};

/** A transparent render of Nora placed by its bottom-centre, with a floor shadow. */
export const Cutout: React.FC<{
  src: string;
  cx: number;
  bottom: number;
  height: number;
  aspect: number; // width / height of the image
  opacity?: number;
  shadow?: number;
  style?: React.CSSProperties;
  flip?: boolean;
}> = ({ src, cx, bottom, height, aspect, opacity = 1, shadow = 0.35, style, flip }) => {
  const w = height * aspect;
  return (
    <div style={{ position: "absolute", left: cx - w / 2, top: bottom - height, width: w, height, opacity, ...style }}>
      <div
        style={{
          position: "absolute",
          left: w * 0.12,
          width: w * 0.76,
          bottom: -height * 0.025,
          height: height * 0.07,
          borderRadius: "50%",
          background: `radial-gradient(ellipse at center, rgba(20,12,10,${shadow}) 0%, rgba(20,12,10,0) 70%)`,
        }}
      />
      <Img
        src={staticFile(src)}
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", transform: flip ? "scaleX(-1)" : undefined }}
      />
    </div>
  );
};

/** A full-frame photographic plate with a slow push and optional focus pull. */
export const Plate: React.FC<{
  src: string;
  scale?: number;
  x?: number;
  y?: number;
  blur?: number;
  brightness?: number;
  opacity?: number;
  origin?: string;
}> = ({ src, scale = 1, x = 0, y = 0, blur = 0, brightness = 1, opacity = 1, origin = "50% 50%" }) => (
  <Abs style={{ overflow: "hidden", opacity }}>
    <Img
      src={staticFile(src)}
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        objectFit: "cover",
        transformOrigin: origin,
        transform: `translate(${x}px, ${y}px) scale(${scale})`,
        filter: `blur(${blur}px) brightness(${brightness})`,
      }}
    />
  </Abs>
);

/** The cable: one thin warm-white line with a soft shadow, drawn on by `draw`. */
export const Cable: React.FC<{
  d: string;
  draw?: number;
  width: number;
  color: string;
  shadow?: number;
  length?: number;
}> = ({ d, draw = 1, width, color, shadow = 0.5, length = 4000 }) => {
  const { w, h } = useFmt();
  return (
    <svg width={w} height={h} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
      <path
        d={d}
        fill="none"
        stroke={`rgba(0,0,0,${shadow})`}
        strokeWidth={width * 2.2}
        strokeLinecap="round"
        strokeDasharray={length}
        strokeDashoffset={length * (1 - clamp01(draw))}
        style={{ filter: `blur(${width * 1.4}px)`, transform: `translate(${width * 0.6}px, ${width * 1.6}px)` }}
      />
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={width}
        strokeLinecap="round"
        strokeDasharray={length}
        strokeDashoffset={length * (1 - clamp01(draw))}
      />
    </svg>
  );
};

/** Nora's presence: the blush dot, pulsing once when `pulseAt` passes. */
export const Presence: React.FC<{ x: number; y: number; r: number; color: string; pulseAt?: number }> = ({
  x,
  y,
  r,
  color,
  pulseAt,
}) => {
  const t = useT();
  const p = pulseAt === undefined ? 1 : prog(t, pulseAt, pulseAt + 0.9);
  const ring = pulseAt !== undefined && t >= pulseAt && p < 1;
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: x - r,
          top: y - r,
          width: r * 2,
          height: r * 2,
          borderRadius: "50%",
          background: color,
        }}
      />
      {ring && (
        <div
          style={{
            position: "absolute",
            left: x - r * (1 + 2.6 * easeOut(p)),
            top: y - r * (1 + 2.6 * easeOut(p)),
            width: r * 2 * (1 + 2.6 * easeOut(p)),
            height: r * 2 * (1 + 2.6 * easeOut(p)),
            borderRadius: "50%",
            border: `${Math.max(1, r * 0.25)}px solid ${color}`,
            opacity: 1 - p,
          }}
        />
      )}
    </>
  );
};

export const Mono: React.FC<React.PropsWithChildren<{ size: number; color: string; style?: React.CSSProperties }>> = ({
  size,
  color,
  style,
  children,
}) => (
  <div
    style={{
      fontFamily: FONT.mono,
      fontSize: size,
      fontWeight: 450,
      color,
      fontFeatureSettings: "'tnum' 1",
      letterSpacing: 0,
      ...style,
    }}
  >
    {children}
  </div>
);

export { CUE, springAt };
