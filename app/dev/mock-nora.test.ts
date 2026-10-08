import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod";
import { handle, handleDocumentState, resetDocumentStates } from "./mock-nora.mjs";

import {
  DoctrineSchema,
  MemorySchema,
  SkillsSchema,
  StatusSchema,
  ToolsSchema,
} from "../src/lib/api/nora";
import { DocumentDetailSchema, DocumentsPageSchema } from "../src/lib/api/documents";
import { PersonProfileSchema, PlanDetailSchema, PlansSchema, RosterSchema } from "../src/lib/api/people";
import {
  DerivedPlacesSchema,
  HistorySchema,
  PresenceEntrySchema,
  RhythmSchema,
  RoutesSchema,
  SavedPlaceSchema,
} from "../src/lib/api/presence";
import {
  ActivityPageSchema,
  BigWalkWorldSchema,
  GameProgressSchema,
  InsightsSchema,
  LocalSaveStatusSchema,
  PlayerDetailSchema,
  PlayersPageSchema,
  SaveHistorySchema,
  ServerSchema,
  ServerStatusSchema,
  SessionsPageSchema,
  WeeklySummarySchema,
  WorldStateSchema,
} from "../src/lib/api/games";

// A fixed instant so every generated field (relative dates, Ken's position
// on his driving loop, ages) is completely reproducible run to run — the
// point of `handle`'s `now` parameter (mock-nora.mjs's own module
// docstring).
const NOW = Date.parse("2026-09-27T12:00:00+08:00");

function call(pathname: string, query: Record<string, string> = {}) {
  return handle(pathname, new URLSearchParams(query), NOW);
}

/** Asserts a 200 whose body parses against `schema`, and returns the parsed
 * value — every schema-validation test below goes through this so a
 * mismatch prints zod's own issues instead of a bare boolean. */
function expectOk<T>(pathname: string, query: Record<string, string>, schema: z.ZodType<T>): T {
  const { status, body } = call(pathname, query);
  expect(status, `${pathname}: expected 200`).toBe(200);
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new Error(`${pathname} did not match its schema:\n${JSON.stringify(parsed.error.issues, null, 2)}`);
  }
  return parsed.data;
}

afterEach(() => {
  delete process.env.MOCK_FAIL;
  delete process.env.MOCK_EMPTY;
});

// ---------------------------------------------------------------- Nora herself

describe("status/memory/doctrine/skills/tools", () => {
  it("GET /status matches StatusSchema", () => {
    expectOk("/app/v1/status", {}, StatusSchema);
  });

  it("GET /memory?personId=miles matches MemorySchema and is scoped to miles", () => {
    const data = expectOk("/app/v1/memory", { personId: "miles" }, MemorySchema);
    expect(data).toHaveLength(1);
    expect(data[0].person_id).toBe("miles");
    expect(data[0].episodes.length).toBeGreaterThan(0);
  });

  it("GET /memory with no personId is the fail-closed empty list, never everyone's data", () => {
    const data = expectOk("/app/v1/memory", {}, MemorySchema);
    expect(data).toEqual([]);
  });

  it("GET /memory?personId=<unknown> is also the empty list", () => {
    const data = expectOk("/app/v1/memory", { personId: "nobody" }, MemorySchema);
    expect(data).toEqual([]);
  });

  it("memory is per-person: miles and ken get different entries", () => {
    const miles = expectOk("/app/v1/memory", { personId: "miles" }, MemorySchema);
    const ken = expectOk("/app/v1/memory", { personId: "ken" }, MemorySchema);
    expect(miles[0].identity).not.toEqual(ken[0].identity);
  });

  it("GET /doctrine matches DoctrineSchema with the six requested sections in order", () => {
    const data = expectOk("/app/v1/doctrine", {}, DoctrineSchema);
    expect(data.map((s) => s.name)).toEqual(["identity", "voice", "silence", "conduct", "capability", "constraints"]);
  });

  it("GET /skills matches SkillsSchema and includes the four real repo skills", () => {
    const data = expectOk("/app/v1/skills", {}, SkillsSchema);
    expect(data.map((s) => s.name).sort()).toEqual([
      "computer-use",
      "document-creation",
      "image-generation",
      "navigation",
    ]);
    for (const skill of data) expect(skill.instructions?.length ?? 0).toBeGreaterThan(0);
  });

  it("GET /tools matches ToolsSchema, ~24 tools, exactly one terminal", () => {
    const data = expectOk("/app/v1/tools", {}, ToolsSchema);
    expect(data.length).toBeGreaterThanOrEqual(20);
    expect(data.length).toBeLessThanOrEqual(28);
    expect(data.filter((t) => t.terminal)).toHaveLength(1);
    expect(data.find((t) => t.terminal)?.name).toBe("send_reply");
    for (const tool of data) expect(tool.budget).toBeGreaterThanOrEqual(1);
    for (const tool of data) expect(tool.budget).toBeLessThanOrEqual(6);
    expect(new Set(data.map((t) => t.source_tier))).toEqual(new Set(["confirmed", "remembered", "web", null]));
  });
});

// ---------------------------------------------------------------- persons

describe("persons", () => {
  it("GET /persons matches RosterSchema, sorted by displayName", () => {
    const data = expectOk("/app/v1/persons", {}, RosterSchema);
    expect(data.length).toBeGreaterThanOrEqual(6);
    const names = data.map((p) => p.displayName);
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
  });

  it("GET /persons/miles matches PersonProfileSchema with abiotic + bigwalk", () => {
    const data = expectOk("/app/v1/persons/miles", {}, PersonProfileSchema);
    expect(data.personId).toBe("miles");
    expect(data.gameProfiles.map((g) => g.serverId).sort()).toEqual(["abiotic", "bigwalk"]);
  });

  it("GET /persons/sam matches PersonProfileSchema with no game profiles", () => {
    const data = expectOk("/app/v1/persons/sam", {}, PersonProfileSchema);
    expect(data.gameProfiles).toEqual([]);
  });

  it("GET /persons/<unknown> is a 404", () => {
    const { status, body } = call("/app/v1/persons/nobody");
    expect(status).toBe(404);
    expect(body).toEqual({ error: "not found" });
  });
});

// ------------------------------------------------------------- documents

describe("documents", () => {
  const LISBON = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01";
  const MEIS = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e04";

  it("lists only what the viewer may read, newest first, matching DocumentsPageSchema", () => {
    const miles = expectOk("/app/v1/documents", { personId: "miles" }, DocumentsPageSchema);
    expect(miles.documents.map((d) => d.title)).toEqual([
      "Lisbon in December",
      "Kitchen rota",
      "From a newer catalog",
      "Saturday packing list",
    ]);
    const mei = expectOk("/app/v1/documents", { personId: "mei" }, DocumentsPageSchema);
    expect(mei.documents.map((d) => d.title)).toEqual(["Lisbon in December", "Mei's surprise"]);
  });

  it("searches titles and text, ignoring case, inside what the viewer may read; labels rooms", () => {
    const find = (personId: string, q: string) =>
      expectOk("/app/v1/documents", { personId, q }, DocumentsPageSchema);
    // The text is only in the body, not the title; the case does not matter.
    expect(find("miles", "DISHES").documents.map((d) => d.title)).toEqual(["Kitchen rota"]);
    expect(find("miles", "lisbon").documents.map((d) => d.title)).toEqual(["Lisbon in December"]);
    // Mei's surprise is Mei's alone: Miles's search for it finds nothing.
    expect(find("miles", "surprise")).toMatchObject({ documents: [], total: 0 });
    expect(find("mei", "surprise").documents.map((d) => d.title)).toEqual(["Mei's surprise"]);
    expect(call("/app/v1/documents", { personId: "miles", q: "x".repeat(201) }).status).toBe(400);
    const all = expectOk("/app/v1/documents", { personId: "miles" }, DocumentsPageSchema);
    const room = Object.fromEntries(all.documents.map((d) => [d.title, d.roomLabel]));
    expect(room["Kitchen rota"]).toBe("Flat chat");
    expect(room["Lisbon in December"]).toBe("");
  });

  it("a person nobody knows, or no person, is a fail-closed 400", () => {
    expect(call("/app/v1/documents").status).toBe(400);
    expect(call("/app/v1/documents", { personId: "nobody" }).status).toBe(400);
  });

  it("an unreadable document answers exactly like an unknown id", () => {
    const hidden = call(`/app/v1/documents/${MEIS}`, { personId: "miles" });
    const unknown = call("/app/v1/documents/00000000-0000-4000-8000-000000000000", { personId: "miles" });
    expect(hidden.status).toBe(404);
    expect(hidden).toEqual(unknown);
  });

  it("serves a readable document whole, and its picture only to a reader who it names", () => {
    const doc = expectOk(`/app/v1/documents/${LISBON}`, { personId: "miles" }, DocumentDetailSchema);
    expect(doc.media).toHaveLength(1);
    const pic = call(`/app/v1/documents/${LISBON}/media/${doc.media[0]}`, { personId: "miles" });
    expect(pic.status).toBe(200);
    expect(pic.contentType).toBe("image/png");
    expect(Buffer.isBuffer(pic.bytes)).toBe(true);
    // Not named by the document: no picture. Not a reader: no picture.
    expect(call(`/app/v1/documents/${LISBON}/media/${"c".repeat(64)}`, { personId: "miles" }).status).toBe(404);
    expect(call(`/app/v1/documents/${MEIS}/media/${doc.media[0]}`, { personId: "miles" }).status).toBe(404);
  });

  it("archive moves a document to the Archived filter, delete hides it everywhere, both like Nora", () => {
    const ROTA = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e02";
    const post = (id: string, state: unknown, personId = "miles") =>
      handleDocumentState(`/app/v1/documents/${id}/state`, new URLSearchParams({ personId }), { state });
    const titles = (state?: string) =>
      expectOk("/app/v1/documents", { personId: "miles", ...(state ? { state } : {}) }, DocumentsPageSchema)
        .documents.map((d) => d.title);
    try {
      expect(post(ROTA, "archived").status).toBe(200);
      expect(titles()).not.toContain("Kitchen rota");
      expect(titles("archived")).toEqual(["Kitchen rota"]);
      expect(post(ROTA, "deleted").status).toBe(200);
      expect(titles("archived")).toEqual([]);
      expect(call(`/app/v1/documents/${ROTA}`, { personId: "miles" }).status).toBe(404);
      // Deleted cannot be restored from the portal, and a reader nobody names cannot change it.
      expect(post(ROTA, "active").status).toBe(404);
      expect(post(MEIS, "archived").status).toBe(404);
      expect(post(LISBON, "purged").status).toBe(400);
      expect(call("/app/v1/documents", { personId: "miles", state: "deleted" }).status).toBe(400);
    } finally {
      // The mock's state is module-level: put the fixture back for every other test.
      resetDocumentStates();
    }
  });

  it("MOCK_FAIL=documents is a 503, never an empty list", () => {
    process.env.MOCK_FAIL = "documents";
    expect(call("/app/v1/documents", { personId: "miles" }).status).toBe(503);
  });
});

// ---------------------------------------------------------------- events

describe("events", () => {
  it("GET /events with no personId is a fail-closed 400", () => {
    const { status, body } = call("/app/v1/events");
    expect(status).toBe(400);
    expect(body).toEqual({ error: "personId required" });
  });

  it("GET /events?personId=<unknown> is also a 400", () => {
    const { status, body } = call("/app/v1/events", { personId: "nobody" });
    expect(status).toBe(400);
    expect(body).toEqual({ error: "personId required" });
  });

  it("GET /events?personId=miles matches PlansSchema and is scoped to miles's events", () => {
    const data = expectOk("/app/v1/events", { personId: "miles" }, PlansSchema);
    expect(data.length).toBeGreaterThan(0);
    expect(data.some((e) => e.title === "Hotpot")).toBe(true);
  });

  it("viewer scoping: sam sees strictly fewer events than miles", () => {
    const miles = expectOk("/app/v1/events", { personId: "miles" }, PlansSchema);
    const sam = expectOk("/app/v1/events", { personId: "sam" }, PlansSchema);
    expect(sam.length).toBeLessThan(miles.length);
    expect(sam.some((e) => e.title.includes("birthday"))).toBe(true);
  });

  it("GET /events/{id}?personId=<participant> matches PlanDetailSchema", () => {
    const list = expectOk("/app/v1/events", { personId: "miles" }, PlansSchema);
    const detail = expectOk(`/app/v1/events/${list[0].id}`, { personId: "miles" }, PlanDetailSchema);
    expect(detail.id).toBe(list[0].id);
    expect(Array.isArray(detail.rsvps)).toBe(true);
  });

  it("GET /events/{id}?personId=<non-participant> is a 404, not the detail", () => {
    // "Mei's birthday dinner" participants are miles/mei/ada/sam — ken is not one.
    const list = expectOk("/app/v1/events", { personId: "mei" }, PlansSchema);
    const birthday = list.find((e) => e.title.includes("birthday"))!;
    const { status, body } = call(`/app/v1/events/${birthday.id}`, { personId: "ken" });
    expect(status).toBe(404);
    expect(body).toEqual({ error: "not found" });
  });

  it("GET /events/<unknown uuid>?personId=miles is a 404", () => {
    const { status } = call("/app/v1/events/00000000-0000-4000-8000-000000000000", { personId: "miles" });
    expect(status).toBe(404);
  });

  it("GET /events/<not a uuid> is a 400, as the real backend answers", () => {
    const { status } = call("/app/v1/events/not-a-real-id", { personId: "miles" });
    expect(status).toBe(400);
  });
});

// ---------------------------------------------------------------- presence

describe("presence", () => {
  it("GET /presence matches an array of PresenceEntrySchema, one per circle member", () => {
    const data = expectOk("/app/v1/presence", {}, z.array(PresenceEntrySchema));
    const ids = data.map((p) => p.personId).sort();
    expect(ids).toEqual(["ada", "jo", "ken", "mei", "miles"]); // sam is not in the circle
    const ken = data.find((p) => p.personId === "ken")!;
    expect(ken.current?.isDriving).toBe(true);
    expect(ken.current?.wireState).toBe("live");
    const mei = data.find((p) => p.personId === "mei")!;
    expect(mei.current?.wireState).toBe("stale");
    const jo = data.find((p) => p.personId === "jo")!;
    expect(jo.current?.wireState).toBe("sharing_off");
  });

  it("GET /presence/places matches an array of SavedPlaceSchema, 4 places", () => {
    const data = expectOk("/app/v1/presence/places", {}, z.array(SavedPlaceSchema));
    expect(data).toHaveLength(4);
  });

  it("GET /presence/ken/history matches HistorySchema with points, clamped hours", () => {
    const data = expectOk("/app/v1/presence/ken/history", { hours: "10000" }, HistorySchema);
    expect(data.hours).toBe(720); // clamped to the 1..720 ceiling
    expect(data.points.length).toBeGreaterThan(0);
    expect(data.points.length).toBeLessThanOrEqual(600);
  });

  it("GET /presence/sam/history matches HistorySchema with empty points", () => {
    const data = expectOk("/app/v1/presence/sam/history", {}, HistorySchema);
    expect(data.points).toEqual([]);
  });

  it("GET /presence/analytics/places matches DerivedPlacesSchema for every circle member", () => {
    const data = expectOk("/app/v1/presence/analytics/places", {}, DerivedPlacesSchema);
    expect(data.subjects.map((s) => s.subjectId).sort()).toEqual(["ada", "jo", "ken", "mei", "miles"]);
    for (const subject of data.subjects) {
      expect(subject.places.length).toBeGreaterThanOrEqual(2);
      expect(subject.places.length).toBeLessThanOrEqual(5);
    }
  });

  it("GET /presence/analytics/rhythm?subjectId=ken matches RhythmSchema with 168 bins", () => {
    const data = expectOk("/app/v1/presence/analytics/rhythm", { subjectId: "ken", days: "30" }, RhythmSchema);
    expect(data.bins).toHaveLength(168);
    expect(data.windowDays).toBe(30);
  });

  it("GET /presence/analytics/rhythm with an unknown subjectId is the empty shape", () => {
    const data = expectOk("/app/v1/presence/analytics/rhythm", { subjectId: "nobody" }, RhythmSchema);
    expect(data.bins).toEqual([]);
  });

  it("GET /presence/analytics/routes?subjectId=ken matches RoutesSchema with 12-25 trips", () => {
    const data = expectOk("/app/v1/presence/analytics/routes", { subjectId: "ken", days: "90" }, RoutesSchema);
    expect(data.trips.length).toBeGreaterThanOrEqual(1);
    expect(data.corridors.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------- games

describe("games", () => {
  it("GET /games/servers matches an array of ServerSchema, three servers", () => {
    const data = expectOk("/app/v1/games/servers", {}, z.array(ServerSchema));
    expect(data.map((s) => s.id).sort()).toEqual(["abiotic", "bigwalk", "palworld"]);
    const bigwalk = data.find((s) => s.id === "bigwalk")!;
    expect(bigwalk.current).toBeNull();
    const palworld = data.find((s) => s.id === "palworld")!;
    expect(palworld.current?.online).toBe(false);
  });

  it("GET /games/servers/abiotic/status matches ServerStatusSchema", () => {
    expectOk("/app/v1/games/servers/abiotic/status", {}, ServerStatusSchema);
  });

  it("GET /games/servers/palworld/status matches ServerStatusSchema (offline)", () => {
    const data = expectOk("/app/v1/games/servers/palworld/status", {}, ServerStatusSchema);
    expect(data.current.online).toBe(false);
    expect(data.current.status).toBe("exited");
  });

  it("GET /games/servers/bigwalk/status matches LocalSaveStatusSchema", () => {
    const data = expectOk("/app/v1/games/servers/bigwalk/status", {}, LocalSaveStatusSchema);
    expect(data.game).toBe("big-walk");
  });

  it("GET /games/servers/<unknown>/status is a 404", () => {
    const { status } = call("/app/v1/games/servers/nope/status");
    expect(status).toBe(404);
  });

  it("GET /games/servers/abiotic/world matches WorldStateSchema", () => {
    expectOk("/app/v1/games/servers/abiotic/world", {}, WorldStateSchema);
  });

  it("GET /games/servers/bigwalk/world matches BigWalkWorldSchema with linked players", () => {
    const data = expectOk("/app/v1/games/servers/bigwalk/world", {}, BigWalkWorldSchema);
    expect(data.game).toBe("big-walk");
    const linked = data.players.filter((p) => p.linkedPerson);
    expect(linked.length).toBeGreaterThan(0);
  });

  it("GET /games/servers/abiotic/graph matches GraphSchema", () => {
    const { status, body } = call("/app/v1/games/servers/abiotic/graph", { hours: "24" });
    expect(status).toBe(200);
    const GraphSchema = z.object({
      hours: z.number(), from: z.number(), to: z.number(),
      data: z.array(
        z.object({
          timestamp: z.number(), online: z.number(), cpuAvg: z.number(), cpuMax: z.number(),
          memoryAvg: z.number(), memoryMax: z.number(), memoryLimit: z.number(),
          networkRxBpsAvg: z.number(), networkTxBpsAvg: z.number(), playerAvg: z.number(), playerMax: z.number(),
        }),
      ),
    });
    expect(GraphSchema.safeParse(body).success).toBe(true);
  });

  it("GET /games/servers/bigwalk/graph returns the empty-data shape", () => {
    const { status, body } = call("/app/v1/games/servers/bigwalk/graph");
    expect(status).toBe(200);
    expect((body as { data: unknown[] }).data).toEqual([]);
  });

  it("GET /games/servers/abiotic/weekly matches WeeklySummarySchema", () => {
    expectOk("/app/v1/games/servers/abiotic/weekly", {}, WeeklySummarySchema);
  });

  it("GET /games/servers/bigwalk/weekly is a 404 (container-shaped route)", () => {
    const { status } = call("/app/v1/games/servers/bigwalk/weekly");
    expect(status).toBe(404);
  });

  it("GET /games/servers/bigwalk/progress matches GameProgressSchema", () => {
    const data = expectOk("/app/v1/games/servers/bigwalk/progress", {}, GameProgressSchema);
    expect(data.game).toBe("big-walk");
  });

  it("GET /games/servers/abiotic/progress is a 404 (bigwalk-only route)", () => {
    const { status } = call("/app/v1/games/servers/abiotic/progress");
    expect(status).toBe(404);
  });

  it("GET /games/servers/bigwalk/save-history matches SaveHistorySchema", () => {
    expectOk("/app/v1/games/servers/bigwalk/save-history", { hours: "168" }, SaveHistorySchema);
  });

  it("GET /games/servers/abiotic/insights matches InsightsSchema with a 5-player leaderboard", () => {
    const data = expectOk("/app/v1/games/servers/abiotic/insights", { days: "30" }, InsightsSchema);
    expect(data.leaderboard).toHaveLength(5);
    expect(data.heatmap).toHaveLength(168);
  });

  it("GET /games/servers/abiotic/players matches PlayersPageSchema", () => {
    const data = expectOk("/app/v1/games/servers/abiotic/players", { page: "1", pageSize: "20" }, PlayersPageSchema);
    expect(data.pagination.total).toBe(5);
  });

  it("GET /games/servers/abiotic/players?online=true only lists online players", () => {
    const data = expectOk(
      "/app/v1/games/servers/abiotic/players",
      { page: "1", pageSize: "20", online: "true" },
      PlayersPageSchema,
    );
    expect(data.data.every((p) => p.online)).toBe(true);
    expect(data.data.map((p) => p.playerName).sort()).toEqual(["Jo", "Ken"]);
  });

  it("GET /games/servers/abiotic/players/{key} matches PlayerDetailSchema with a full save", () => {
    const data = expectOk(
      "/app/v1/games/servers/abiotic/players/steam_74822881012340001",
      {},
      PlayerDetailSchema,
    );
    expect(data.save).toBeDefined();
    expect(data.vitalsHistory?.length).toBe(40);
    expect(data.linkedPerson?.personId).toBe("ken");
  });

  it("GET /games/servers/abiotic/players/{key} for a player with no save omits it entirely", () => {
    const data = expectOk(
      "/app/v1/games/servers/abiotic/players/steam_74822881012340004",
      {},
      PlayerDetailSchema,
    );
    expect(data.save).toBeUndefined();
    expect(data.linkedPerson).toBeUndefined();
  });

  it("GET /games/servers/abiotic/players/<unknown-key> is a 404", () => {
    const { status } = call("/app/v1/games/servers/abiotic/players/steam_0");
    expect(status).toBe(404);
  });

  it("GET /games/servers/abiotic/activity matches ActivityPageSchema, newest first", () => {
    const data = expectOk("/app/v1/games/servers/abiotic/activity", {}, ActivityPageSchema);
    expect(data.data.length).toBeGreaterThan(0);
    const timestamps = data.data.map((e) => e.occurredAt);
    expect(timestamps).toEqual([...timestamps].sort((a, b) => b - a));
  });

  it("GET /games/servers/abiotic/sessions matches SessionsPageSchema with one open session per online player", () => {
    const data = expectOk("/app/v1/games/servers/abiotic/sessions", { pageSize: "100" }, SessionsPageSchema);
    const open = data.data.filter((s) => s.leftAt === null);
    expect(open).toHaveLength(2); // ken + jo are the online players
    for (const s of open) {
      expect(s.durationMs).toBeNull();
      expect(s.closedReason).toBeNull();
    }
  });
});

// ---------------------------------------------------------------- 404 default

it("an unknown route is a 404 with the shared not-found body", () => {
  const { status, body } = call("/app/v1/this-route-does-not-exist");
  expect(status).toBe(404);
  expect(body).toEqual({ error: "not found" });
});

// ---------------------------------------------------------------- MOCK_FAIL / MOCK_EMPTY

describe("MOCK_FAIL and MOCK_EMPTY", () => {
  it("MOCK_FAIL=games,presence answers 503 for those families and leaves others alone", () => {
    process.env.MOCK_FAIL = "games,presence";
    expect(call("/app/v1/games/servers")).toEqual({ status: 503, body: { error: "unavailable" } });
    expect(call("/app/v1/presence")).toEqual({ status: 503, body: { error: "unavailable" } });
    expect(call("/app/v1/status").status).toBe(200);
  });

  it("MOCK_EMPTY=presence,tools empties those families' data without erroring", () => {
    process.env.MOCK_EMPTY = "presence,tools";
    const presence = expectOk("/app/v1/presence", {}, z.array(PresenceEntrySchema));
    expect(presence).toEqual([]);
    const tools = expectOk("/app/v1/tools", {}, ToolsSchema);
    expect(tools).toEqual([]);
    // "status" was not in MOCK_EMPTY, so it stays fully populated.
    const status = expectOk("/app/v1/status", {}, StatusSchema);
    expect(status.reminders.count).toBeGreaterThan(0);
  });

  it("MOCK_EMPTY=status empties the reminders block", () => {
    process.env.MOCK_EMPTY = "status";
    const status = expectOk("/app/v1/status", {}, StatusSchema);
    expect(status.reminders.count).toBe(0);
    expect(status.reminders.upcoming).toEqual([]);
    expect(status.reminders.next_due_at).toBeNull();
  });
});
