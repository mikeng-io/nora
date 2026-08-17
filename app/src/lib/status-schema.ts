import { z } from "zod";

// Mirrors nora/web/status.py::build_status_payload's return shape exactly
// (design spec §3.3: hand-written zod instead of OpenAPI/codegen).
export const StatusSchema = z.object({
  uptime_seconds: z.number(),
  store: z.object({ reachable: z.boolean() }),
  honcho: z.object({ reachable: z.boolean() }),
  reminders: z.object({
    count: z.number(),
    next_due_at: z.string().nullable(),
    upcoming: z.array(
      z.object({
        due_at: z.string(),
        platform: z.string(),
        conversation_id: z.string(),
      }),
    ),
  }),
});

export type Status = z.infer<typeof StatusSchema>;

export async function fetchStatus(): Promise<Status> {
  const res = await fetch("/api/status", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`status fetch failed: ${res.status}`);
  }
  return StatusSchema.parse(await res.json());
}
