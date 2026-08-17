import { z } from "zod";

// Wire-format schemas for Pulse's `pulse_server_status` MCP tool — reached
// through Nora's own web backend (nora/web/games.py), not Pulse's HTTP API
// directly (see that module's docstring for why: the REST API needs a
// second Pulse-issued token Nora was never given, and its own README frames
// MCP as Nora's intended, least-privileged path).
//
// This shape is real, observed data — captured live from
// https://orion.tail0a1b2c3d.ts.net/pulse/mcp on 2026-08-17 — not adapted
// from Pulse's REST API types. It's deliberately coarser than a Docker
// cgroup read: no memory limit, network/block I/O, pids, or restart count,
// because `pulse_server_status` doesn't report those (it's an LLM/human
// -facing summary tool, not a raw container-metrics endpoint). There is
// also no hourly time-series MCP tool at all — `fetchServerGraph` below
// stays wired for when one exists, but the backend always answers 503 for
// it today, which the graph card already renders as "no history yet".

export const ServerMetricsSchema = z.object({
  online: z.boolean(),
  status: z.string(),
  uptimeHours: z.number(),
  cpuPercent: z.number(),
  memoryGb: z.number(),
  playerCount: z.number(),
  lastSampleAt: z.string(),
});

/** Server identity only — the shape nested inside `/status`. */
export const ServerSummarySchema = z.object({
  id: z.string(),
  displayName: z.string(),
  color: z.string(),
  maxPlayers: z.number().optional(),
});

/** `/v1/servers` additionally embeds the latest sample (null before first poll). */
export const ServerSchema = ServerSummarySchema.extend({
  current: ServerMetricsSchema.nullable(),
});

export const ServerStatusSchema = z.object({
  server: ServerSummarySchema,
  current: ServerMetricsSchema,
  activePlayers: z.array(z.string()),
});

export const GraphPointSchema = z.object({
  timestamp: z.number(),
  online: z.number(),
  cpuAvg: z.number(),
  cpuMax: z.number(),
  memoryAvg: z.number(),
  memoryMax: z.number(),
  memoryLimit: z.number(),
  networkRxBpsAvg: z.number(),
  networkTxBpsAvg: z.number(),
  playerAvg: z.number(),
  playerMax: z.number(),
});

export const GraphSchema = z.object({
  hours: z.number(),
  from: z.number(),
  to: z.number(),
  data: z.array(GraphPointSchema),
});

export type ServerMetrics = z.infer<typeof ServerMetricsSchema>;
export type ServerSummary = z.infer<typeof ServerSummarySchema>;
export type Server = z.infer<typeof ServerSchema>;
export type ServerStatus = z.infer<typeof ServerStatusSchema>;
export type GraphPoint = z.infer<typeof GraphPointSchema>;
export type Graph = z.infer<typeof GraphSchema>;

const ServersListSchema = z.array(ServerSchema);

export async function fetchServers(): Promise<Server[]> {
  const res = await fetch("/api/games/servers", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`games servers fetch failed: ${res.status}`);
  }
  return ServersListSchema.parse(await res.json());
}

export async function fetchServerStatus(id: string): Promise<ServerStatus> {
  const res = await fetch(`/api/games/servers/${encodeURIComponent(id)}/status`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`game server status fetch failed: ${res.status}`);
  }
  return ServerStatusSchema.parse(await res.json());
}

export async function fetchServerGraph(id: string, hours = 24): Promise<Graph> {
  const res = await fetch(
    `/api/games/servers/${encodeURIComponent(id)}/graph?hours=${hours}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`game server graph fetch failed: ${res.status}`);
  }
  return GraphSchema.parse(await res.json());
}
