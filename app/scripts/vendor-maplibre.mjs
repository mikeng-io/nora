// MapLibre runs its tile parsing in a module worker it expects to find
// beside its own bundle (`./maplibre-gl-worker.mjs`, resolved from
// import.meta.url). A bundler moves the bundle and not the worker, so the
// worker and the shared chunk it imports are served from public/ instead,
// copied from the installed package so their version always matches, and
// named in src/components/map/nora-map.tsx via setWorkerUrl. Runs before
// `dev` and `build` (package.json); the output is gitignored.
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "node_modules", "maplibre-gl", "dist");
const out = join(root, "public", "vendor", "maplibre");
mkdirSync(out, { recursive: true });
for (const f of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) copyFileSync(join(src, f), join(out, f));
const { version } = JSON.parse(readFileSync(join(root, "node_modules", "maplibre-gl", "package.json"), "utf8"));
console.log(`vendor-maplibre: worker ${version} -> public/vendor/maplibre/`);
