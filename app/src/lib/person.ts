import type { CSSProperties } from "react";

// A person keeps one colour everywhere in the portal: their avatar, their
// pin and route on the map (DESIGN.md, the Person-Hue Rule). The colour is
// an OKLCH hue at one fixed lightness and chroma per lighting, so no person
// reads louder than another, and the hues stay at least 25° from the
// reserved ones: danger and blush (17-31), caution (80), success (166),
// denim (265). A pure display hash: never persisted, never sent anywhere.
//
// Hue alone cannot tell seven people apart for every reader (equal
// lightness is exactly what colour-vision deficiency cannot separate, and
// the dataviz validator says so), so a person's colour always travels with
// their initials or name. It is never the only thing that says who.
export const PERSON_HUES = [110, 225, 320, 140, 295, 195, 345] as const;

/** OKLCH lightness and chroma per lighting; globals.css mirrors these. */
export const PERSON_LC = { light: [0.56, 0.12], dark: [0.7, 0.11] } as const;

export function hueOf(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return PERSON_HUES[h % PERSON_HUES.length];
}

/** CSS variables a component reads as `var(--person)` / `var(--person-wash)`. */
export function personStyle(id: string): CSSProperties {
  const h = hueOf(id);
  return {
    ["--person" as string]: `oklch(var(--person-l) var(--person-c) ${h})`,
    ["--person-wash" as string]: `oklch(var(--person-wash-l) 0.03 ${h})`,
  } as CSSProperties;
}

/** The same colour as sRGB hex, for a canvas (the map) that cannot read
 * CSS variables. */
export function personColorFor(id: string, theme: "light" | "dark"): string {
  const [L, C] = PERSON_LC[theme];
  return oklchToHex(L, C, hueOf(id));
}

export function oklchToHex(L: number, C: number, hueDeg: number): string {
  const h = (hueDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  const enc = (x: number) => {
    const c = Math.max(0, Math.min(1, x));
    return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
  };
  return `#${rgb.map((x) => Math.round(enc(x) * 255).toString(16).padStart(2, "0")).join("")}`;
}

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  // A CJK name reads best as its first character; a Latin one as initials.
  if (/[㐀-鿿]/.test(words[0][0])) return words[0][0];
  return words
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}
