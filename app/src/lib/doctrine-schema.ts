import { z } from "zod";

// Mirrors nora/web/doctrine.py::build_doctrine_payload's return shape —
// name/description/version/body, doctrine order (identity, voice, silence,
// conduct, capability, constraints). Never the internal wiring fields
// (authority_above/below, change_policy, sha256, path) — those aren't
// reader-facing.
export const DoctrineSectionSchema = z.object({
  name: z.string(),
  description: z.string(),
  version: z.number(),
  body: z.string(),
});

export const DoctrineSchema = z.array(DoctrineSectionSchema);

export type DoctrineSection = z.infer<typeof DoctrineSectionSchema>;

export async function fetchDoctrine(): Promise<DoctrineSection[]> {
  const res = await fetch("/api/doctrine", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`doctrine fetch failed: ${res.status}`);
  }
  return DoctrineSchema.parse(await res.json());
}
