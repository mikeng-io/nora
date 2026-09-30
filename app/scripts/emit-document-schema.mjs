// Writes nora/documents/document.schema.json, catalog-prompt.md and corpus.json from the
// catalog. Run: npm run documents:emit (Node >= 22.6 for type stripping).
// documents.test.ts fails when either committed file is stale.
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const { emitSchema, emitPrompt, emitCorpus } = await import("../src/lib/documents/emit.ts");
const { VALID, INVALID } = await import("../src/lib/documents/corpus.ts");
const out = fileURLToPath(new URL("../../nora/documents/", import.meta.url));
await mkdir(out, { recursive: true });
await writeFile(out + "document.schema.json", emitSchema());
await writeFile(out + "catalog-prompt.md", emitPrompt());
await writeFile(out + "corpus.json", emitCorpus({ valid: VALID, invalid: INVALID }));
console.log("wrote", out);
