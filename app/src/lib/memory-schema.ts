import { z } from "zod";

// Mirrors nora/web/memory.py::build_memory_payload's return shape exactly.
// `identity` and `episodes` are Honcho's two DISTINCT reads of a person
// (see that module's docstring): identity claims (name, aliases) vs dated
// episodes (what happened) — read live, they never overlap.
export const MemoryEntrySchema = z.object({
  person_id: z.string(),
  display_name: z.string(),
  reachable: z.boolean(),
  identity: z.array(z.string()),
  episodes: z.array(z.string()),
});

export const MemorySchema = z.array(MemoryEntrySchema);

export type MemoryEntry = z.infer<typeof MemoryEntrySchema>;

export async function fetchMemory(): Promise<MemoryEntry[]> {
  const res = await fetch("/api/memory", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`memory fetch failed: ${res.status}`);
  }
  return MemorySchema.parse(await res.json());
}
