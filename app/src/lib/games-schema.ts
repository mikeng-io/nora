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

// --- The rest of Pulse's REST surface: weekly/world/insights/players/
// sessions/activity — schemas below are shaped from REAL responses
// captured live against https://orion.tail0a1b2c3d.ts.net/pulse/api/v1 on
// 2026-08-19 (not from game-server-monitor's TypeScript source, which this
// repo doesn't vendor) — see nora/web/games.py's module docstring for the
// backend side of this same contract. Every route here is a thin pass-
// through on Nora's `/app/v1/games/servers/{id}/...` API, itself a thin
// pass-through on Pulse's own REST API.

const PaginationSchema = z.object({
  page: z.number(),
  pageSize: z.number(),
  total: z.number(),
  hasMore: z.boolean(),
});

/** A localized, possibly-unresolved game entity — an item, structure,
 * sector, trait, skill, or kill-subject id. `known: false` means Pulse
 * could not resolve `id` to a display name (a new game version, a modded
 * entity) — `en`/`zhHant` fall back to the raw id in that case, and the UI
 * should render it dimmed/monospace rather than trust it as a real name
 * (matches game-server-monitor's own "known vs unknown" convention). */
export const NamedRefSchema = z.object({
  id: z.string(),
  en: z.string(),
  zhHant: z.string(),
  known: z.boolean(),
});
export type NamedRef = z.infer<typeof NamedRefSchema>;

const CountedRefSchema = NamedRefSchema.extend({ count: z.number() });

const DeathCauseSchema = z.object({
  cause: z.string(),
  count: z.number(),
  causeZhHant: z.string(),
});
export type DeathCause = z.infer<typeof DeathCauseSchema>;

const PlayerSummarySchema = z.object({
  serverId: z.string(),
  playerKey: z.string(),
  playerId: z.string(),
  playerName: z.string(),
  online: z.boolean(),
  sessionCount: z.number(),
  totalPlaytimeMs: z.number(),
  longestSessionMs: z.number(),
  deaths: z.number(),
  topDeathCause: z.object({ cause: z.string(), count: z.number() }).nullable(),
  firstSeenAt: z.number(),
  lastSeenAt: z.number(),
});
export type PlayerSummary = z.infer<typeof PlayerSummarySchema>;

export const PlayersPageSchema = z.object({
  data: z.array(PlayerSummarySchema),
  pagination: PaginationSchema,
});
export type PlayersPage = z.infer<typeof PlayersPageSchema>;

const SkillSchema = z.object({
  index: z.number(),
  xp: z.number(),
  level: z.number(),
  progress: z.number(),
  xpIntoLevel: z.number(),
  xpForLevel: z.number(),
  en: z.string(),
  zhHant: z.string(),
});
export type Skill = z.infer<typeof SkillSchema>;

// `radiation`/`bodyTemperature` are absent from a real `vitalsHistory`
// point whenever the save had no reading for that meter yet (observed
// live: `radiation` present on 81/156 points, `bodyTemperature` on
// 63/156) — optional, not always-present the way the other four are.
const SurvivalVitalsSchema = z.object({
  hunger: z.number(),
  thirst: z.number(),
  sanity: z.number(),
  fatigue: z.number(),
  continence: z.number(),
  radiation: z.number().optional(),
  bodyTemperature: z.number().optional(),
});
export type SurvivalVitals = z.infer<typeof SurvivalVitalsSchema>;

const LimbHealthSchema = z.object({
  head: z.number(),
  torso: z.number(),
  leftArm: z.number(),
  rightArm: z.number(),
  leftLeg: z.number(),
  rightLeg: z.number(),
});
export type LimbHealth = z.infer<typeof LimbHealthSchema>;

const PlayerSaveSchema = z.object({
  savedAt: z.number(),
  job: z.object({ en: z.string(), zhHant: z.string() }),
  traits: z.array(
    z.object({ id: z.string(), en: z.string(), zhHant: z.string(), negative: z.boolean() }),
  ),
  equipment: z.array(NamedRefSchema.extend({ durability: z.number() })),
  skills: z.array(SkillSchema),
  killCounts: z.array(CountedRefSchema),
  limbHealth: LimbHealthSchema,
  survival: SurvivalVitalsSchema,
  money: z.number(),
  buffs: z.array(z.unknown()),
  location: NamedRefSchema,
  collections: z.object({ crafted: z.number(), pickedUp: z.number(), fish: z.number() }),
});
export type PlayerSave = z.infer<typeof PlayerSaveSchema>;

// `limbHealth` itself is occasionally absent from a point too (observed
// live: 2/156) — same "not every meter sampled every save" reasoning as
// the optional fields inside `SurvivalVitalsSchema`.
const VitalsHistoryPointSchema = z.object({
  savedAt: z.number(),
  limbHealth: LimbHealthSchema.optional(),
  survival: SurvivalVitalsSchema,
  money: z.number(),
});
export type VitalsHistoryPoint = z.infer<typeof VitalsHistoryPointSchema>;

const StatEventSchema = z.object({
  kind: z.string(),
  subject: NamedRefSchema,
  delta: z.number(),
  occurredAt: z.number(),
});
export type StatEvent = z.infer<typeof StatEventSchema>;

const SkillProgressSchema = z.object({
  days: z.number(),
  totalXp: z.number(),
  skills: z.array(z.object({ index: z.number(), xp: z.number(), en: z.string(), zhHant: z.string() })),
});
export type SkillProgress = z.infer<typeof SkillProgressSchema>;

// `save`/`vitalsHistory`/`statEvents`/`skillProgress` are absent wholesale
// for a player Pulse has never captured a save snapshot for yet (observed
// live: a player with one short session and no in-game save poll landing
// during it) — the character sheet/vitals/combat panels must all treat
// this as "no save data yet", not assume every player has one.
export const PlayerDetailSchema = PlayerSummarySchema.extend({
  deathCauses: z.array(DeathCauseSchema),
  save: PlayerSaveSchema.optional(),
  vitalsHistory: z.array(VitalsHistoryPointSchema).optional(),
  statEvents: z.array(StatEventSchema).optional(),
  skillProgress: SkillProgressSchema.optional(),
});
export type PlayerDetail = z.infer<typeof PlayerDetailSchema>;

// `type` selects which optional fields are populated: `player.joined`/
// `player.left` carry only playerId/playerName; `player.died` additionally
// carries deathCause/message (+ zhHant pairs); `world.event` carries only
// message/messageZhHant, no player fields at all.
export const ActivityEventSchema = z.object({
  id: z.number(),
  serverId: z.string(),
  occurredAt: z.number(),
  observedAt: z.number(),
  type: z.enum(["player.joined", "player.left", "player.died", "world.event"]),
  playerId: z.string().optional(),
  playerName: z.string().optional(),
  deathCause: z.string().optional(),
  deathCauseZhHant: z.string().optional(),
  message: z.string().optional(),
  messageZhHant: z.string().optional(),
});
export type ActivityEvent = z.infer<typeof ActivityEventSchema>;

export const ActivityPageSchema = z.object({
  data: z.array(ActivityEventSchema),
  pagination: PaginationSchema,
});
export type ActivityPage = z.infer<typeof ActivityPageSchema>;

// `leftAt`/`durationMs`/`closedReason` are null for a still-open session —
// never observed live (nobody online at capture time) but this is Pulse's
// documented shape for an in-progress session (game-server-monitor's own
// SessionList highlights exactly this null-`leftAt` case as "online").
export const SessionSchema = z.object({
  id: z.number(),
  serverId: z.string(),
  playerKey: z.string(),
  playerId: z.string(),
  playerName: z.string(),
  joinedAt: z.number(),
  leftAt: z.number().nullable(),
  closedReason: z.string().nullable(),
  durationMs: z.number().nullable(),
});
export type Session = z.infer<typeof SessionSchema>;

export const SessionsPageSchema = z.object({
  data: z.array(SessionSchema),
  pagination: PaginationSchema,
});
export type SessionsPage = z.infer<typeof SessionsPageSchema>;

export const WeeklySummarySchema = z.object({
  weekStart: z.number(),
  weekEnd: z.number(),
  uniquePlayers: z.number(),
  sessions: z.number(),
  playtimeMs: z.number(),
  deaths: z.number(),
  peakPlayers: z.number(),
  availabilityPercent: z.number(),
  activityCoverageStartedAt: z.number(),
});
export type WeeklySummary = z.infer<typeof WeeklySummarySchema>;

const StockpileSectorSchema = NamedRefSchema.extend({
  sectorId: z.string(),
  structures: z.number(),
  storedItems: z.number(),
});

export const WorldStateSchema = z.object({
  savedAt: z.number(),
  metadata: z.object({
    minutesPassed: z.number(),
    day: z.number(),
    storyProgress: z.string(),
  }),
  stockpile: z.object({
    items: z.array(CountedRefSchema),
    structures: z.array(CountedRefSchema),
    sectors: z.array(StockpileSectorSchema),
    totalItems: z.number(),
    totalStructures: z.number(),
    distinctItems: z.number(),
  }),
  timeline: z.array(NamedRefSchema.extend({ day: z.number() })),
});
export type WorldState = z.infer<typeof WorldStateSchema>;

const LeaderboardEntrySchema = z.object({
  playerKey: z.string(),
  playerName: z.string(),
  sessions: z.number(),
  playtimeMs: z.number(),
  longestSessionMs: z.number(),
  deaths: z.number(),
  deathsPerHour: z.number(),
});
export type LeaderboardEntry = z.infer<typeof LeaderboardEntrySchema>;

const HeatmapCellSchema = z.object({
  weekday: z.number(),
  hour: z.number(),
  playerHours: z.number(),
});
export type HeatmapCell = z.infer<typeof HeatmapCellSchema>;

const CoplayEntrySchema = z.object({
  aKey: z.string(),
  aName: z.string(),
  bKey: z.string(),
  bName: z.string(),
  overlapMs: z.number(),
});
export type CoplayEntry = z.infer<typeof CoplayEntrySchema>;

const KillLeaderboardEntrySchema = z.object({
  playerKey: z.string(),
  playerName: z.string(),
  totalKills: z.number(),
  kills: z.array(CountedRefSchema),
});
export type KillLeaderboardEntry = z.infer<typeof KillLeaderboardEntrySchema>;

const LearnerLeaderboardEntrySchema = z.object({
  playerKey: z.string(),
  playerName: z.string(),
  xp: z.number(),
  topSkill: z.object({ index: z.number(), xp: z.number(), en: z.string(), zhHant: z.string() }),
});
export type LearnerLeaderboardEntry = z.infer<typeof LearnerLeaderboardEntrySchema>;

export const InsightsSchema = z.object({
  days: z.number(),
  from: z.number(),
  leaderboard: z.array(LeaderboardEntrySchema),
  deathCauses: z.array(DeathCauseSchema),
  heatmap: z.array(HeatmapCellSchema),
  coplay: z.array(CoplayEntrySchema),
  killLeaderboard: z.array(KillLeaderboardEntrySchema),
  learnerLeaderboard: z.array(LearnerLeaderboardEntrySchema),
});
export type Insights = z.infer<typeof InsightsSchema>;

async function getJson(path: string): Promise<unknown> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path} fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchServerWeekly(id: string): Promise<WeeklySummary> {
  return WeeklySummarySchema.parse(
    await getJson(`/api/games/servers/${encodeURIComponent(id)}/weekly`),
  );
}

export async function fetchServerWorld(id: string): Promise<WorldState> {
  return WorldStateSchema.parse(
    await getJson(`/api/games/servers/${encodeURIComponent(id)}/world`),
  );
}

export async function fetchServerInsights(id: string, days = 30): Promise<Insights> {
  return InsightsSchema.parse(
    await getJson(`/api/games/servers/${encodeURIComponent(id)}/insights?days=${days}`),
  );
}

export async function fetchServerPlayers(
  id: string, opts: { page?: number; pageSize?: number; online?: boolean } = {},
): Promise<PlayersPage> {
  const params = new URLSearchParams();
  if (opts.page) params.set("page", String(opts.page));
  if (opts.pageSize) params.set("pageSize", String(opts.pageSize));
  if (opts.online !== undefined) params.set("online", String(opts.online));
  const qs = params.toString();
  return PlayersPageSchema.parse(
    await getJson(`/api/games/servers/${encodeURIComponent(id)}/players${qs ? `?${qs}` : ""}`),
  );
}

export async function fetchServerPlayer(id: string, playerKey: string): Promise<PlayerDetail> {
  return PlayerDetailSchema.parse(
    await getJson(
      `/api/games/servers/${encodeURIComponent(id)}/players/${encodeURIComponent(playerKey)}`,
    ),
  );
}

export async function fetchServerActivity(
  id: string, opts: { page?: number; pageSize?: number } = {},
): Promise<ActivityPage> {
  const params = new URLSearchParams();
  if (opts.page) params.set("page", String(opts.page));
  if (opts.pageSize) params.set("pageSize", String(opts.pageSize));
  const qs = params.toString();
  return ActivityPageSchema.parse(
    await getJson(`/api/games/servers/${encodeURIComponent(id)}/activity${qs ? `?${qs}` : ""}`),
  );
}

export async function fetchServerSessions(
  id: string,
  opts: { page?: number; pageSize?: number; playerKey?: string } = {},
): Promise<SessionsPage> {
  const params = new URLSearchParams();
  if (opts.page) params.set("page", String(opts.page));
  if (opts.pageSize) params.set("pageSize", String(opts.pageSize));
  if (opts.playerKey) params.set("playerKey", opts.playerKey);
  const qs = params.toString();
  return SessionsPageSchema.parse(
    await getJson(`/api/games/servers/${encodeURIComponent(id)}/sessions${qs ? `?${qs}` : ""}`),
  );
}
