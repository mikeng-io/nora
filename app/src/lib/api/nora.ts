import { z } from "zod";
import { apiGet } from "./client";

// Nora herself: status, what she remembers about the viewer, her doctrine,
// her tools and skills. Each schema mirrors its builder in nora/web/*.py
// exactly; hand-written zod rather than codegen (design spec §3.3).

// nora/web/status.py::build_status_payload. `upcoming` never carries a
// reminder's text or who asked: those are the requester's own words.
export const StatusSchema = z.object({
  uptime_seconds: z.number(),
  store: z.object({ reachable: z.boolean() }),
  honcho: z.object({ reachable: z.boolean() }),
  reminders: z.object({
    count: z.number(),
    next_due_at: z.string().nullable(),
    upcoming: z.array(
      z.object({ due_at: z.string(), platform: z.string(), conversation_id: z.string() }),
    ),
  }),
});
export type Status = z.infer<typeof StatusSchema>;

// nora/web/memory.py::build_memory_payload: the caller's own entries only,
// one per Nora workspace they belong to (DEC-214), each labelled
// `workspace`. `identity` (who they are) and `episodes` (what happened) are
// two distinct reads and never collapse into one list.
// `profile` is optional: only a backend running the Profile switch
// (`[profile] render`) sends it — the caller's own account per unit, every
// chain head with its state, the evidence behind each line, and the open
// disputes (cognition-loop/50 §4).
export const ProfileEvidenceSchema = z.object({
  ref: z.string(),
  kind: z.enum(["message", "episode", "social", "rollup", "unknown"]),
  text: z.string().nullable().optional(),
  said_at: z.number().optional(),
  by: z.string().nullable().optional(),
});
export type ProfileEvidence = z.infer<typeof ProfileEvidenceSchema>;
export const ProfileLineSchema = z.object({
  part: z.enum(["facts", "traits", "guesses"]),
  kind: z.string(),
  text: z.string(),
  mode: z.string(),
  state: z.string(),
  told_by: z.string().nullable(),
  written_at: z.number(),
  fact_key: z.string().nullable().optional(),
  fact_value: z.unknown().optional(),
  as_of: z.number().nullable().optional(),
  valid_from: z.number().nullable().optional(),
  valid_until: z.number().nullable().optional(),
  evidence: z.array(ProfileEvidenceSchema),
  prediction: z.string().nullable().optional(),
  prediction_observable: z.string().nullable().optional(),
  prediction_window_end: z.number().nullable().optional(),
  verdict: z.string().nullable().optional(),
});
export type ProfileLine = z.infer<typeof ProfileLineSchema>;
export const ProfileDisputeSchema = z.object({
  kind: z.string(),
  claim: z.string(),
  disputed_by: z.string().nullable(),
  written_at: z.number(),
});
export type ProfileDispute = z.infer<typeof ProfileDisputeSchema>;
export const ProfileAccountSchema = z.object({
  card: z.string().optional(),
  unit: z.string(),
  lines: z.array(ProfileLineSchema),
  disputes: z.array(ProfileDisputeSchema),
});
export type ProfileAccount = z.infer<typeof ProfileAccountSchema>;
export const MemoryEntrySchema = z.object({
  workspace: z.string().optional(),
  person_id: z.string(),
  display_name: z.string(),
  reachable: z.boolean(),
  identity: z.array(z.string()),
  episodes: z.array(z.string()),
  profile: z.array(ProfileAccountSchema).optional(),
});
export type MemoryEntry = z.infer<typeof MemoryEntrySchema>;
export const MemorySchema = z.array(MemoryEntrySchema);

// nora/web/doctrine.py::build_doctrine_payload, in doctrine order.
export const DoctrineSectionSchema = z.object({
  name: z.string(),
  description: z.string(),
  version: z.number(),
  body: z.string(),
});
export type DoctrineSection = z.infer<typeof DoctrineSectionSchema>;
export const DoctrineSchema = z.array(DoctrineSectionSchema);

// nora/web/skills.py::build_skills_payload. `instructions` is optional: a
// backend older than PR #93 lists name and description only.
export const SkillSchema = z.object({
  name: z.string(),
  description: z.string(),
  instructions: z.string().optional(),
});
export type Skill = z.infer<typeof SkillSchema>;
export const SkillsSchema = z.array(SkillSchema);

// nora/web/tools.py::build_tools_payload. `description` is optional because
// a tool Strands cannot introspect still lists with its declared fields.
export const ToolSchema = z.object({
  name: z.string(),
  description: z.string().optional(),
  budget: z.number(),
  terminal: z.boolean(),
  time_shiftable: z.boolean().nullable(),
  source_tier: z.string().nullable(),
  cacheable: z.boolean(),
  read_only: z.boolean(),
  side_effect_state: z.string(),
  idempotency: z.string(),
  retry: z.string(),
  output_contract: z.string().nullable(),
});
export type Tool = z.infer<typeof ToolSchema>;
export const ToolsSchema = z.array(ToolSchema);

export const fetchStatus = (signal?: AbortSignal) => apiGet("/status", StatusSchema, { signal });
export const fetchMemory = (signal?: AbortSignal) => apiGet("/memory", MemorySchema, { signal });
export const fetchDoctrine = (signal?: AbortSignal) =>
  apiGet("/doctrine", DoctrineSchema, { signal });
export const fetchSkills = (signal?: AbortSignal) => apiGet("/skills", SkillsSchema, { signal });
export const fetchTools = (signal?: AbortSignal) => apiGet("/tools", ToolsSchema, { signal });
