// Writes nora/documents/document.schema.json and catalog-prompt.md from the
// catalog. Run: npm run documents:emit (Node >= 22.6 for type stripping).
// documents.test.ts fails when either committed file is stale.
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const { emitSchema, emitPrompt } = await import("../src/lib/documents/emit.ts");
const out = fileURLToPath(new URL("../../nora/documents/", import.meta.url));
await mkdir(out, { recursive: true });
await writeFile(out + "document.schema.json", emitSchema());
await writeFile(out + "catalog-prompt.md", emitPrompt());
console.log("wrote", out);
