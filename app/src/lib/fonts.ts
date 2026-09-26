import localFont from "next/font/local";

// Self-hosted from the repo (no request to a font service at build or run
// time). DESIGN.md §4: Inter for the interface, JetBrains Mono for values,
// Space Grotesk for her name only (the file is subset to the four glyphs of
// "Nora", the site's own subset). CJK comes from @fontsource-variable/
// noto-sans-hk in globals.css, sliced by unicode-range so a page downloads
// only the characters it shows.
export const inter = localFont({
  src: "../fonts/inter-latin-opsz.woff2",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
  fallback: ["system-ui", "sans-serif"],
});

export const jetbrains = localFont({
  src: "../fonts/jetbrains-mono-latin.woff2",
  variable: "--font-jetbrains",
  weight: "100 800",
  display: "swap",
  preload: false,
  fallback: ["ui-monospace", "monospace"],
});

export const spaceGrotesk = localFont({
  src: "../fonts/space-grotesk-nora.woff2",
  variable: "--font-space-grotesk",
  weight: "300 700",
  display: "block",
});
