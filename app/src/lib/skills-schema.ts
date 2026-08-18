import { z } from "zod";

// Mirrors nora/web/skills.py::build_skills_payload's return shape — name
// and description only, never the instructions body (progressive
// disclosure: the body reaches the model only on activation, and has no
// business reaching a browser either).
export const SkillEntrySchema = z.object({
  name: z.string(),
  description: z.string(),
});

export const SkillsSchema = z.array(SkillEntrySchema);

export type SkillEntry = z.infer<typeof SkillEntrySchema>;

export async function fetchSkills(): Promise<SkillEntry[]> {
  const res = await fetch("/api/skills", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`skills fetch failed: ${res.status}`);
  }
  return SkillsSchema.parse(await res.json());
}
