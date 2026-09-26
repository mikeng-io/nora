// Nora's public site. Static output, no adapter, no client framework: every
// page is HTML + CSS plus small vanilla scripts (the chat demo, the lighting
// and language choices, the character-sheet viewer, her notebook, the loop's
// signal, sound, and Nora herself in src/scripts/nora/), each progressive:
// the page reads in full without them.
import { defineConfig, fontProviders } from "astro/config";
import { cjkCoverage } from "./scripts/cjk-coverage.mjs";

const fontsource = (pkg, file) => `./node_modules/@fontsource-variable/${pkg}/files/${file}`;

export default defineConfig({
  // Canonical origin for <link rel=canonical>, hreflang and og:url. The
  // portal and this site share it (the site answers `/`, `/en/` and
  // `/_astro/*`; everything else is the portal): site/README.md.
  site: process.env.SITE_ORIGIN ?? "https://nora.67.zone",
  trailingSlash: "always",
  build: { format: "directory", inlineStylesheets: "auto" },
  compressHTML: true,
  prefetch: false,
  devToolbar: { enabled: false },
  fonts: [
    {
      // Inter with its optical-size axis: display cuts at heading sizes,
      // text cuts at body sizes, from one 71 KB file (DESIGN.md §4).
      provider: fontProviders.local(),
      name: "Inter",
      cssVariable: "--font-inter",
      fallbacks: ["Noto Sans HK", "PingFang HK", "system-ui", "sans-serif"],
      options: {
        variants: [{ weight: "100 900", style: "normal", src: [fontsource("inter", "inter-latin-opsz-normal.woff2")] }],
      },
    },
    {
      // The wordmark face, subset to the four glyphs of "Nora" (1.2 KB):
      // DESIGN.md allows Space Grotesk for her name and nothing else, so
      // the file cannot be used for anything else either.
      provider: fontProviders.local(),
      name: "Space Grotesk",
      cssVariable: "--font-wordmark",
      fallbacks: ["sans-serif"],
      options: {
        variants: [{ weight: "300 700", style: "normal", src: ["./src/fonts/space-grotesk-nora.woff2"] }],
      },
    },
    {
      provider: fontProviders.local(),
      name: "JetBrains Mono",
      cssVariable: "--font-mono",
      fallbacks: ["monospace"],
      options: {
        variants: [
          {
            weight: "100 800",
            style: "normal",
            src: [fontsource("jetbrains-mono", "jetbrains-mono-latin-wght-normal.woff2")],
          },
        ],
      },
    },
  ],
  integrations: [cjkCoverage()],
  vite: {
    // The art lives in the repo's own assets/ (DESIGN.md §2 names those
    // files as her sources), one level above this package.
    server: { fs: { allow: [".."] } },
  },
});
