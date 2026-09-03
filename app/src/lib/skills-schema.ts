import { z } from "zod";

// Mirrors nora/web/skills.py::build_skills_payload's return shape — each
// entry is the skill's public metadata: name, the when-to-load description
// (the only body text that ever sits in the prompt), and the instructions
// body (progressive disclosure: the body reaches the model only on
// activation; the portal shows it because the portal is private and the
// operator wants to read what a skill actually says).
export const SkillEntrySchema = z.object({
  name: z.string(),
  description: z.string(),
  // Optional: the deployed backend may predate the instructions-body change
  // (PR #93). The portal degrades to name + description until the backend
  // catches up; the body appears once deployed.
  instructions: z.string().optional(),
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
