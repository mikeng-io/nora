import { z } from "zod";
import { apiGet } from "./client";

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

// Local-save games (Big Walk, and any future one) have no container, so
// no `current` — /status answers with the last upload's own facts instead
// (game-server-monitor@67ce88a, AGENTS.md's "abstracted server model").
export const LocalSaveStatusSchema = z.object({
  server: ServerSummarySchema,
  game: z.literal("big-walk"),
  lastUploadedAt: z.number(),
  lastPlayedAt: z.number().optional(),
  totalPlaytimeSeconds: z.number().optional(),
  activePlayers: z.array(z.string()),
  activeProfiles: z.array(ActivePlayerSchema).optional(),
});
export type LocalSaveStatus = z.infer<typeof LocalSaveStatusSchema>;

export type AnyServerStatus =
  | { kind: "container"; status: ServerStatus }
  | { kind: "local-save"; status: LocalSaveStatus };

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
export type GameSkill = z.infer<typeof SkillSchema>;

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

// Named for the ONE game whose save shape this actually is — Pulse
// currently monitors only Abiotic Factor, and this schema is that game's
// job/traits/limbHealth/survival/skills fields, not a general "player
// save" contract. A second game adapter would need its own schema here,
// not a widened version of this one.
const AbioticFactorSaveSchema = z.object({
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
export type AbioticFactorSave = z.infer<typeof AbioticFactorSaveSchema>;

// `limbHealth` (observed live: 2/156) and `survival` itself (observed
// live: 1/76 on a second player's real history) are each occasionally
// absent from a point — same "not every meter sampled every save"
// reasoning as the optional fields inside `SurvivalVitalsSchema`. `savedAt`
// and `money` were 100% present across both captures, so those stay
// required rather than guessed-optional.
const VitalsHistoryPointSchema = z.object({
  savedAt: z.number(),
  limbHealth: LimbHealthSchema.optional(),
  survival: SurvivalVitalsSchema.optional(),
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
//
// `save`'s `.catch(undefined)` is the OTHER reason it can be missing: a
// second, non-Abiotic-Factor game adapter's save blob won't match
// `AbioticFactorSaveSchema` at all. Rather than letting one field's shape
// mismatch fail `PlayerDetailSchema.parse()` and blank the whole profile
// page, an unrecognized save degrades to "no save data" — the same
// state a genuinely absent save already renders as everywhere below.
export const PlayerDetailSchema = PlayerSummarySchema.extend({
  deathCauses: z.array(DeathCauseSchema),
  save: AbioticFactorSaveSchema.optional().catch(undefined),
  vitalsHistory: z.array(VitalsHistoryPointSchema).optional(),
  statEvents: z.array(StatEventSchema).optional(),
  skillProgress: SkillProgressSchema.optional(),
  // Set server-side (nora/web/games.py) only when the response's steamId
  // resolves through identity.toml (`persons.resolve("pulse", steamId)`)
  // — the user↔platform↔game cross-reference, absent for anyone not yet
  // linked or without a steamId Pulse could capture.
  linkedPerson: z.object({ personId: z.string(), displayName: z.string() }).optional(),
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

// Big Walk (game-server-monitor@67ce88a) — a completely different
// game-specific `/world` shape from Abiotic Factor's above, since Pulse's
// server model is now game-neutral (AGENTS.md): a local-save game has no
// container, no stockpile/sectors, and its own progress vocabulary
// instead (gourds/big keys/FM stations/lookout lights/named doors).
const BigWalkPlayerLookSchema = z.object({
  steamId: z.string(),
  head: z.number().optional(),
  torso: z.number().optional(),
  legs: z.number().optional(),
  walkingProven: z.boolean().optional(),
  linkedPerson: z.object({ personId: z.string(), displayName: z.string() }).optional(),
});
export type BigWalkPlayerLook = z.infer<typeof BigWalkPlayerLookSchema>;

export const BigWalkWorldSchema = z.object({
  game: z.literal("big-walk"),
  savedAt: z.number(),
  slotName: z.string(),
  filenameUid: z.string(),
  worldSeed: z.number(),
  lastPlayedAt: z.number(),
  totalPlaytimeSeconds: z.number(),
  skipAidsActive: z.boolean(),
  players: z.array(BigWalkPlayerLookSchema),
  completions: z.object({
    gourds: z.record(z.string(), z.number()),
    bigKeys: z.record(z.string(), z.number()),
    fmStations: z.record(z.string(), z.number()),
    lookoutLights: z.record(z.string(), z.number()),
    namedDoors: z.record(z.string(), z.number()),
  }),
  uuidFlags: z.record(z.string(), z.number()),
  notes: z.record(z.string(), z.string()),
  inventory: z.array(z.string()),
});
export type BigWalkWorld = z.infer<typeof BigWalkWorldSchema>;

export type AnyWorldState =
  | { kind: "abiotic-factor"; world: WorldState }
  | { kind: "big-walk"; world: BigWalkWorld };

export const GameProgressSchema = z.object({
  game: z.literal("big-walk"),
  slotName: z.string(),
  lastPlayedAt: z.number(),
  totalPlaytimeSeconds: z.number(),
  totalPlaytimeHours: z.number(),
  playerCount: z.number(),
  inventoryCount: z.number(),
  completionCounts: z.object({
    gourds: z.number(),
    bigKeys: z.number(),
    fmStations: z.number(),
    lookoutLights: z.number(),
    namedDoors: z.number(),
    uuidFlags: z.number(),
  }),
});
export type GameProgress = z.infer<typeof GameProgressSchema>;

const SaveHistoryPointSchema = z.object({
  uploadedAt: z.number(),
  hash: z.string(),
  totalPlaytimeSeconds: z.number().optional(),
  completedGourds: z.number(),
  completedBigKeys: z.number(),
  completedFmStations: z.number(),
  completedLookoutLights: z.number(),
  completedNamedDoors: z.number(),
  completedUuidFlags: z.number(),
  playerCount: z.number(),
  inventoryCount: z.number(),
});
export type SaveHistoryPoint = z.infer<typeof SaveHistoryPointSchema>;

export const SaveHistorySchema = z.object({
  hours: z.number(),
  from: z.number(),
  to: z.number(),
  data: z.array(SaveHistoryPointSchema),
});
export type SaveHistory = z.infer<typeof SaveHistorySchema>;

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


// ---------------------------------------------------------------- fetchers
// Every route is /app/v1/games/servers/... on Nora (nora/web/games.py),
// itself a thin pass-through onto Pulse.

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const srv = (id: string) => `/games/servers/${encodeURIComponent(id)}`;

export const fetchServers = (signal?: AbortSignal) =>
  apiGet("/games/servers", z.array(ServerSchema), { signal });

const AnyStatusSchema = z.unknown().transform((body, ctx): AnyServerStatus => {
  if (isRecord(body) && body.game === "big-walk") {
    const r = LocalSaveStatusSchema.safeParse(body);
    if (r.success) return { kind: "local-save", status: r.data };
    ctx.addIssue({ code: "custom", message: "local-save status" });
    return z.NEVER;
  }
  const r = ServerStatusSchema.safeParse(body);
  if (r.success) return { kind: "container", status: r.data };
  ctx.addIssue({ code: "custom", message: "container status" });
  return z.NEVER;
});

export const fetchServerStatus = (id: string, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/status`, AnyStatusSchema, { signal });

export const fetchServerGraph = (id: string, hours: number, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/graph`, GraphSchema, { query: { hours }, signal });

export const fetchServerWeekly = (id: string, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/weekly`, WeeklySummarySchema, { signal });

const AnyWorldSchema = z.unknown().transform((body, ctx): AnyWorldState => {
  if (isRecord(body) && body.game === "big-walk") {
    const r = BigWalkWorldSchema.safeParse(body);
    if (r.success) return { kind: "big-walk", world: r.data };
  } else {
    const r = WorldStateSchema.safeParse(body);
    if (r.success) return { kind: "abiotic-factor", world: r.data };
  }
  ctx.addIssue({ code: "custom", message: "world state" });
  return z.NEVER;
});

export const fetchServerWorld = (id: string, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/world`, AnyWorldSchema, { signal });

export const fetchServerProgress = (id: string, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/progress`, GameProgressSchema, { signal });

export const fetchServerSaveHistory = (id: string, hours: number, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/save-history`, SaveHistorySchema, { query: { hours }, signal });

export const fetchServerInsights = (id: string, days: number, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/insights`, InsightsSchema, { query: { days }, signal });

export const fetchServerPlayers = (
  id: string,
  opts: { page?: number; pageSize?: number; online?: boolean } = {},
  signal?: AbortSignal,
) => apiGet(`${srv(id)}/players`, PlayersPageSchema, { query: opts, signal });

export const fetchServerPlayer = (id: string, playerKey: string, signal?: AbortSignal) =>
  apiGet(`${srv(id)}/players/${encodeURIComponent(playerKey)}`, PlayerDetailSchema, { signal });

export const fetchServerActivity = (
  id: string,
  opts: { page?: number; pageSize?: number } = {},
  signal?: AbortSignal,
) => apiGet(`${srv(id)}/activity`, ActivityPageSchema, { query: opts, signal });

export const fetchServerSessions = (
  id: string,
  opts: { page?: number; pageSize?: number; playerKey?: string } = {},
  signal?: AbortSignal,
) => apiGet(`${srv(id)}/sessions`, SessionsPageSchema, { query: opts, signal });
