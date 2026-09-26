// Render stills at given times for one composition, and a contact sheet.
//   node pipeline/stills.mjs <composition-id> <out-dir> <t1> <t2> ...
// Bundles once. The sheet is what gets looked at between edits.
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

const [id, out, ...times] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
const composition = await selectComposition({ serveUrl, id });
const files = [];
for (const s of times) {
  const frame = Math.min(composition.durationInFrames - 1, Math.round(Number(s) * composition.fps));
  const file = path.join(out, `${id}-${String(frame).padStart(3, "0")}.png`);
  await renderStill({ composition, serveUrl, output: file, frame, imageFormat: "png" });
  files.push(file);
}
const per = composition.width > composition.height ? 4 : 6;
const size = composition.width > composition.height ? "640x" : "300x";
const rows = [];
for (let i = 0; i < files.length; i += per) {
  const row = path.join(out, `${id}-row${i}.png`);
  execFileSync("magick", [...files.slice(i, i + per).flatMap((f) => [f, "-resize", size, "-bordercolor", "#222", "-border", "4"]), "+append", row]);
  rows.push(row);
}
execFileSync("magick", [...rows, "-background", "#222", "-append", path.join(out, `${id}-sheet.png`)]);
console.log(path.join(out, `${id}-sheet.png`));
