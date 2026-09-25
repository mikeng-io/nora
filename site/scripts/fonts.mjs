// Regenerates the Noto Sans HK subset from every CJK character in src/.
// Run it after changing Chinese copy (`npm run fonts`); `astro build` fails
// if the built pages use a character the committed subset does not have.
// The guard reads the committed .txt, not the woff2: a character the source
// font itself lacked would be listed yet have no glyph, and fall back to
// PingFang HK or the system sans. Noto Sans HK covers every character used today.
//
// Source: Google Fonts' variable Noto Sans HK (OFL), 11 MB, cached in .cache/.
import { readFile, writeFile, mkdir, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import subsetFont from "subset-font";
import { cjkChars } from "./cjk.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_URL = "https://github.com/google/fonts/raw/main/ofl/notosanshk/NotoSansHK%5Bwght%5D.ttf";
const CACHE = join(ROOT, ".cache", "NotoSansHK-wght.ttf");
const OUT = join(ROOT, "src", "fonts", "noto-sans-hk-subset.woff2");
const CHARS = join(ROOT, "src", "fonts", "noto-sans-hk-subset.txt");

async function walk(dir) {
  const out = [];
  for (const name of await readdir(dir)) {
    const path = join(dir, name);
    if ((await stat(path)).isDirectory()) out.push(...(await walk(path)));
    else if (/\.(astro|ts|mjs|css)$/.test(name)) out.push(path);
  }
  return out;
}

if (!existsSync(CACHE)) {
  await mkdir(dirname(CACHE), { recursive: true });
  const res = await fetch(SOURCE_URL);
  if (!res.ok) throw new Error(`download failed: ${res.status} ${SOURCE_URL}`);
  await writeFile(CACHE, Buffer.from(await res.arrayBuffer()));
}

const chars = new Set();
for (const file of await walk(join(ROOT, "src"))) {
  for (const c of cjkChars(await readFile(file, "utf8"))) chars.add(c);
}
const text = [...chars].sort().join("");
const font = await subsetFont(await readFile(CACHE), text, {
  targetFormat: "woff2",
  preserveNameIds: [0, 1, 2, 3, 4, 5, 6, 13, 14],
});
await writeFile(OUT, font);
await writeFile(CHARS, text + "\n");
console.log(`noto-sans-hk-subset: ${chars.size} characters, ${(font.length / 1024).toFixed(1)} KB`);
