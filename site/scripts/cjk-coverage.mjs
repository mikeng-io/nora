// Build guard: every CJK character on a built page must be in the committed
// Noto Sans HK subset. A missing glyph would not error in a browser, it would
// quietly render in a different font, so the build fails instead.
import { readFile, readdir, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { cjkChars } from "./cjk.mjs";

async function htmlFiles(dir) {
  const out = [];
  for (const name of await readdir(dir)) {
    const path = join(dir, name);
    if ((await stat(path)).isDirectory()) out.push(...(await htmlFiles(path)));
    else if (name.endsWith(".html")) out.push(path);
  }
  return out;
}

export function cjkCoverage() {
  return {
    name: "nora:cjk-coverage",
    hooks: {
      "astro:build:done": async ({ dir, logger }) => {
        const root = fileURLToPath(dir);
        const charsFile = new URL("../src/fonts/noto-sans-hk-subset.txt", import.meta.url);
        const have = cjkChars(await readFile(charsFile, "utf8"));
        const missing = new Map();
        const pages = await htmlFiles(root);
        // Nothing to check is not a pass: a build that emitted no pages
        // should not report that every page is covered.
        if (pages.length === 0) throw new Error(`CJK coverage: no built pages under ${root}.`);
        for (const file of pages) {
          for (const c of cjkChars(await readFile(file, "utf8"))) {
            if (!have.has(c)) missing.set(c, file.slice(root.length));
          }
        }
        if (missing.size) {
          const list = [...missing].map(([c, f]) => `${c} (${f})`).join(", ");
          throw new Error(`CJK subset is missing ${missing.size} character(s): ${list}. Run \`npm run fonts\`.`);
        }
        logger.info(`CJK subset covers all ${pages.length} pages (${have.size} characters).`);
      },
    },
  };
}
