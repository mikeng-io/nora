import { z } from "zod";

// Wire-format schemas for Pulse's documented HTTP API (game-server-monitor's
// README §"HTTP API": GET /v1/servers, GET /v1/servers/{id}/status,
// GET /v1/servers/{id}/graph?hours=24). Adapted from that repo's own
// web/src/lib/pulse-schemas.ts, narrowed to what the README actually
// documents — Pulse's real app additionally serves players/activity/
// sessions/insights/world endpoints, but those aren't part of the surface
// this module is built against (design spec §5.3, "designed now, built in
// v2" — build against the documented shapes, not the sibling app's full
// route table).
//
// The README lists `/graph?hours=24` next to the JSON `/status` and
// `/servers` routes, so it's treated as JSON here too; the reference app's
// actual route for equivalent data is named `/history` and returns the same
// point shape (its `HistorySchema`), which is what `GraphSchema` below
// mirrors under the README's own name for the endpoint.

export const ContainerMetricsSchema = z.object({
  serverId: z.string(),
  timestamp: z.number(),
  online: z.boolean(),
  status: z.string(),
  cpuPercent: z.number(),
  memoryBytes: z.number(),
  memoryLimitBytes: z.number(),
  networkRxBytes: z.number(),
  networkTxBytes: z.number(),
  networkRxBps: z.number(),
  networkTxBps: z.number(),
  blockReadBytes: z.number(),
  blockWriteBytes: z.number(),
  pids: z.number(),
  restartCount: z.number(),
  playerCount: z.number(),
  startedAt: z.string().optional(),
  image: z.string().optional(),
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
  current: ContainerMetricsSchema.nullable(),
});

export const ActivePlayerSchema = z.object({
  playerKey: z.string(),
  playerName: z.string(),
});

export const ServerStatusSchema = z.object({
  server: ServerSummarySchema,
  current: ContainerMetricsSchema,
  activePlayers: z.array(z.string()),
  // Optional so a Pulse predating this field still validates.
  activeProfiles: z.array(ActivePlayerSchema).optional(),
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

export type ContainerMetrics = z.infer<typeof ContainerMetricsSchema>;
export type ServerSummary = z.infer<typeof ServerSummarySchema>;
export type Server = z.infer<typeof ServerSchema>;
export type ActivePlayer = z.infer<typeof ActivePlayerSchema>;
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
