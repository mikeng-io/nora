import { z } from "zod";

// Mirrors nora/web/tools.py::build_tools_payload's return shape — each
// entry is the model-facing description (Args-stripped, exactly what the
// agent loop derives) plus the declared registry fields the runtime
// enforces. `description` is optional because a tool Strands cannot
// introspect still lists with its declared fields (R-27: one bad tool
// never sinks the whole endpoint).
export const ToolEntrySchema = z.object({
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

export const ToolsSchema = z.array(ToolEntrySchema);

export type ToolEntry = z.infer<typeof ToolEntrySchema>;

export async function fetchTools(): Promise<ToolEntry[]> {
  const res = await fetch("/api/tools", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`tools fetch failed: ${res.status}`);
  }
  return ToolsSchema.parse(await res.json());
}

// Display helpers — the declared registry values are machine vocabulary;
// the portal renders friendly labels without restating the model-facing
// description.
export function sideEffectLabel(value: string): string {
  switch (value) {
    case "none":
      return "Cannot write outside the turn";
    case "confirmed":
      return "Writes are confirmed";
    case "unknown":
      return "Write outcome unknown on timeout";
    default:
      return value;
  }
}

export function tierLabel(value: string | null): string {
  switch (value) {
    case "confirmed":
      return "Live data";
    case "remembered":
      return "Household memory";
    case "web":
      return "Web";
    default:
      return "Action / pure function";
  }
}
