import { describe, expect, it } from "vitest";
import {
  ActivityEventSchema,
  BigWalkWorldSchema,
  GameProgressSchema,
  GraphSchema,
  InsightsSchema,
  LocalSaveStatusSchema,
  PlayerDetailSchema,
  PlayersPageSchema,
  SaveHistorySchema,
  ServerSchema,
  ServerStatusSchema,
  SessionSchema,
  WeeklySummarySchema,
  WorldStateSchema,
} from "./games-schema";

const CURRENT = {
  serverId: "abiotic_factor",
  timestamp: 1_755_000_000_000,
  online: true,
  status: "running",
  cpuPercent: 12.4,
  memoryBytes: 2_147_483_648,
  memoryLimitBytes: 8_589_934_592,
  networkRxBytes: 1000,
  networkTxBytes: 2000,
  networkRxBps: 100,
  networkTxBps: 200,
  blockReadBytes: 0,
  blockWriteBytes: 0,
  pids: 12,
  restartCount: 0,
  playerCount: 2,
  startedAt: "2026-08-17T00:00:00Z",
  image: "abiotic-factor:latest",
};

describe("ServerSchema", () => {
  it("parses a real /v1/servers list entry", () => {
    const body = {
      id: "abiotic_factor",
      displayName: "Abiotic Factor",
      color: "#78dcca",
      maxPlayers: 8,
      current: CURRENT,
    };
    expect(() => ServerSchema.parse(body)).not.toThrow();
  });

  it("allows current: null before the first poll", () => {
    const body = { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#78dcca", current: null };
    expect(() => ServerSchema.parse(body)).not.toThrow();
  });

  it("rejects an entry missing displayName", () => {
    expect(() => ServerSchema.parse({ id: "abiotic_factor", color: "#78dcca", current: null })).toThrow();
  });
});

describe("ServerStatusSchema", () => {
  it("parses a real /v1/servers/{id}/status response", () => {
    const body = {
      server: { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#78dcca", maxPlayers: 8 },
      current: CURRENT,
      activePlayers: ["Miles", "Ada"],
      activeProfiles: [
        { playerKey: "p1", playerName: "Miles" },
        { playerKey: "p2", playerName: "Ada" },
      ],
    };
    expect(() => ServerStatusSchema.parse(body)).not.toThrow();
  });

  it("parses without activeProfiles, for a Pulse predating that field", () => {
    const body = {
      server: { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#78dcca" },
      current: CURRENT,
      activePlayers: [],
    };
    expect(() => ServerStatusSchema.parse(body)).not.toThrow();
  });

  it("rejects a response missing current", () => {
    expect(() =>
      ServerStatusSchema.parse({
        server: { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#78dcca" },
        activePlayers: [],
      }),
    ).toThrow();
  });
});

describe("GraphSchema", () => {
  it("parses a real /v1/servers/{id}/graph?hours=24 response", () => {
    const body = {
      hours: 24,
      from: 1_754_913_600_000,
      to: 1_755_000_000_000,
      data: [
        {
          timestamp: 1_754_913_600_000,
          online: 1,
          cpuAvg: 10.2,
          cpuMax: 22.1,
          memoryAvg: 1_000_000,
          memoryMax: 1_200_000,
          memoryLimit: 8_589_934_592,
          networkRxBpsAvg: 500,
          networkTxBpsAvg: 700,
          playerAvg: 1.5,
          playerMax: 3,
        },
      ],
    };
    expect(() => GraphSchema.parse(body)).not.toThrow();
  });

  it("parses an empty data array before Pulse has any history", () => {
    const body = { hours: 24, from: 0, to: 0, data: [] };
    expect(() => GraphSchema.parse(body)).not.toThrow();
  });

  it("rejects a data point missing playerMax", () => {
    const body = {
      hours: 24,
      from: 0,
      to: 0,
      data: [
        {
          timestamp: 0,
          online: 1,
          cpuAvg: 0,
          cpuMax: 0,
          memoryAvg: 0,
          memoryMax: 0,
          memoryLimit: 0,
          networkRxBpsAvg: 0,
          networkTxBpsAvg: 0,
          playerAvg: 0,
        },
      ],
    };
    expect(() => GraphSchema.parse(body)).toThrow();
  });
});

// Fixtures below are trimmed excerpts of REAL responses captured live
// against https://orion.tail0a1b2c3d.ts.net/pulse/api/v1 on 2026-08-19 —
// not invented shapes. Full untrimmed captures live in the session
// scratchpad; these keep just enough of each to exercise every optional
// field's both-present and both-absent cases.

describe("WeeklySummarySchema", () => {
  it("parses a real /weekly response", () => {
    const body = {
      weekStart: 1786957200000, weekEnd: 1787562000000, uniquePlayers: 0,
      sessions: 0, playtimeMs: 0, deaths: 0, peakPlayers: 0,
      availabilityPercent: 100, activityCoverageStartedAt: 1786957200000,
    };
    expect(() => WeeklySummarySchema.parse(body)).not.toThrow();
  });
});

describe("PlayersPageSchema", () => {
  it("parses a real /players page", () => {
    const body = {
      data: [
        {
          serverId: "abiotic_factor", playerKey: "id:eos:0002aa", playerId: "eos:0002aa",
          playerName: "山田花子.하나", online: false, sessionCount: 24,
          totalPlaytimeMs: 158984057, longestSessionMs: 13478890, deaths: 47,
          topDeathCause: { cause: "forgot about gravity.", count: 12 },
          firstSeenAt: 1784309646931, lastSeenAt: 1786724759486,
        },
      ],
      pagination: { page: 1, pageSize: 3, total: 6, hasMore: true },
    };
    expect(() => PlayersPageSchema.parse(body)).not.toThrow();
  });

  it("allows a null topDeathCause for a player with zero deaths", () => {
    const body = {
      data: [
        {
          serverId: "abiotic_factor", playerKey: "id:eos:0003", playerId: "eos:0003",
          playerName: "Fresh", online: false, sessionCount: 1, totalPlaytimeMs: 100,
          longestSessionMs: 100, deaths: 0, topDeathCause: null,
          firstSeenAt: 1, lastSeenAt: 2,
        },
      ],
      pagination: { page: 1, pageSize: 20, total: 1, hasMore: false },
    };
    expect(() => PlayersPageSchema.parse(body)).not.toThrow();
  });
});

describe("PlayerDetailSchema", () => {
  const BASE_PLAYER = {
    serverId: "abiotic_factor", playerKey: "id:eos:0002", playerId: "eos:0002",
    playerName: "oPineCone", online: false, sessionCount: 57,
    totalPlaytimeMs: 301032073, longestSessionMs: 14096518, deaths: 39,
    topDeathCause: { cause: "forgot about gravity.", count: 7 },
    firstSeenAt: 1784472574856, lastSeenAt: 1786724763031,
    deathCauses: [{ cause: "forgot about gravity.", count: 7, causeZhHant: "忘記地心吸力，墮下身亡。" }],
  };

  it("parses a real full player detail, including a save snapshot", () => {
    const body = {
      ...BASE_PLAYER,
      save: {
        savedAt: 1786724764281,
        job: { en: "Epimedical Bionomicist", zhHant: "醫療生態學家" },
        traits: [{ id: "Trait_SelfDefense", en: "Self Defense", zhHant: "自衛術", negative: false }],
        equipment: [
          { id: "armor_chest_tech_u1", en: "armor_chest_tech_u1", zhHant: "armor_chest_tech_u1", known: false, durability: 0.964 },
        ],
        skills: [
          { index: 0, xp: 59372.7, level: 16, progress: 0.86, xpIntoLevel: 7742, xpForLevel: 8977, en: "Sprinting", zhHant: "衝刺" },
        ],
        killCounts: [
          { id: "ElectroPest", en: "Electropest", zhHant: "電擊害蟲", known: true, count: 3 },
        ],
        limbHealth: { head: 70, torso: 100, leftArm: 100, rightArm: 100, leftLeg: 100, rightLeg: 100 },
        survival: { hunger: 89, thirst: 83.9, sanity: 100, fatigue: 6.3, continence: 54, radiation: 2.9 },
        money: 103,
        buffs: [],
        location: { id: "Facility_Office1", en: "Office Sector", zhHant: "辦公區", known: true },
        collections: { crafted: 137, pickedUp: 727, fish: 1 },
      },
      vitalsHistory: [
        // radiation/bodyTemperature both absent — the common case.
        {
          savedAt: 1786529398369,
          limbHealth: { head: 66, torso: 100, leftArm: 100, rightArm: 100, leftLeg: 100, rightLeg: 100 },
          survival: { hunger: 106.9, thirst: 106.5, sanity: 100, fatigue: 17.8, continence: 74 },
          money: 94,
        },
        // limbHealth itself absent — observed live on 2/156 real points.
        {
          savedAt: 1786529500000,
          survival: { hunger: 100, thirst: 100, sanity: 100, fatigue: 0, continence: 100 },
          money: 95,
        },
        // survival itself absent — observed live on 1/76 real points from a
        // second player's history (miles_novak, 2026-08-19).
        {
          savedAt: 1786724632923,
          limbHealth: { head: 64, torso: 100, leftArm: 100, rightArm: 86.7, leftLeg: 100, rightLeg: 100 },
          money: 109,
        },
      ],
      statEvents: [
        { kind: "crafted", subject: { id: "laser_collector", en: "laser_collector", zhHant: "laser_collector", known: false }, delta: 1, occurredAt: 1785470366713 },
      ],
      skillProgress: {
        days: 7, totalXp: 65149.3,
        skills: [{ index: 13, xp: 23905, en: "Agriculture", zhHant: "農業" }],
      },
    };
    expect(() => PlayerDetailSchema.parse(body)).not.toThrow();
  });

  it("parses a player Pulse has never captured a save snapshot for", () => {
    // Real shape: save/vitalsHistory/statEvents/skillProgress absent
    // wholesale for a player with too short/rare a session to be polled.
    expect(() => PlayerDetailSchema.parse(BASE_PLAYER)).not.toThrow();
  });

  it("degrades an unrecognized save shape to undefined instead of failing the whole parse", () => {
    // A future non-Abiotic-Factor game adapter's save blob won't match
    // AbioticFactorSaveSchema — this must not blank the whole profile page.
    const body = { ...BASE_PLAYER, save: { totallyDifferentGameShape: true } };
    const parsed = PlayerDetailSchema.parse(body);
    expect(parsed.save).toBeUndefined();
  });
});

describe("ActivityEventSchema", () => {
  it.each([
    ["player.joined", { id: 1, serverId: "s", occurredAt: 1, observedAt: 1, type: "player.joined", playerId: "p", playerName: "n" }],
    ["player.left", { id: 2, serverId: "s", occurredAt: 1, observedAt: 1, type: "player.left", playerId: "p", playerName: "n" }],
    ["player.died", {
      id: 3, serverId: "s", occurredAt: 1, observedAt: 1, type: "player.died", playerId: "p", playerName: "n",
      deathCause: "was lost to the abyss.", deathCauseZhHant: "墮入深淵身亡。",
      message: "was lost to the abyss.", messageZhHant: "墮入深淵身亡。",
    }],
    ["world.event", {
      id: 4, serverId: "s", occurredAt: 1, observedAt: 1, type: "world.event",
      message: "All portal world resources have reset.", messageZhHant: "所有傳送門世界的資源均已重置。",
    }],
  ])("parses a real %s event", (_type, body) => {
    expect(() => ActivityEventSchema.parse(body)).not.toThrow();
  });
});

describe("SessionSchema", () => {
  it("parses a real closed session", () => {
    const body = {
      id: 180, serverId: "abiotic_factor", playerKey: "id:eos:0002c1", playerId: "eos:0002c1",
      playerName: "Clara", joinedAt: 1786719544038, leftAt: 1786724423700,
      closedReason: "leave", durationMs: 4879662,
    };
    expect(() => SessionSchema.parse(body)).not.toThrow();
  });

  it("parses a still-open session (leftAt/closedReason/durationMs null)", () => {
    const body = {
      id: 181, serverId: "abiotic_factor", playerKey: "id:eos:0002c1", playerId: "eos:0002c1",
      playerName: "Clara", joinedAt: 1786719544038,
      leftAt: null, closedReason: null, durationMs: null,
    };
    expect(() => SessionSchema.parse(body)).not.toThrow();
  });
});

describe("WorldStateSchema", () => {
  it("parses a real /world response", () => {
    const body = {
      savedAt: 1786726722336,
      metadata: { minutesPassed: 39197, day: 164, storyProgress: "EndGame" },
      stockpile: {
        items: [{ id: "scrap_metal", en: "Metal Scrap", zhHant: "金屬碎片", known: true, count: 322 }],
        structures: [{ id: "Deployed_Lamp_Wall_Crafted", en: "Deployed_Lamp_Wall_Crafted", zhHant: "Deployed_Lamp_Wall_Crafted", known: false, count: 63 }],
        sectors: [
          { id: "Facility", en: "Facility", zhHant: "設施", known: true, sectorId: "Facility", structures: 569, storedItems: 6649 },
        ],
        totalItems: 6649, totalStructures: 569, distinctItems: 301,
      },
      timeline: [{ id: "Facility_Office3", en: "Office Sector 3", zhHant: "辦公區 3", known: true, day: 2 }],
    };
    expect(() => WorldStateSchema.parse(body)).not.toThrow();
  });
});

describe("InsightsSchema", () => {
  it("parses a real /insights response", () => {
    const body = {
      days: 30, from: 1784467500416,
      leaderboard: [
        { playerKey: "id:eos:0002", playerName: "oPineCone", sessions: 57, playtimeMs: 301032073, longestSessionMs: 14096518, deaths: 39, deathsPerHour: 0.466 },
      ],
      deathCauses: [{ cause: "forgot about gravity.", count: 46, causeZhHant: "忘記地心吸力，墮下身亡。" }],
      heatmap: [{ weekday: 0, hour: 22, playerHours: 10.5 }],
      coplay: [{ aKey: "id:eos:0002aa", aName: "山田花子.하나", bKey: "id:eos:0002e2", bName: "熱咖啡", overlapMs: 151772596 }],
      killLeaderboard: [
        { playerKey: "id:eos:0002e2", playerName: "熱咖啡", totalKills: 87, kills: [{ id: "ElectroPest", en: "Electropest", zhHant: "電擊害蟲", known: true, count: 55 }] },
      ],
      learnerLeaderboard: [
        { playerKey: "id:eos:0002", playerName: "oPineCone", xp: 247681.1, topSkill: { index: 1, xp: 49822.7, en: "Strength", zhHant: "力量" } },
      ],
    };
    expect(() => InsightsSchema.parse(body)).not.toThrow();
  });
});

// Big Walk (game-server-monitor@67ce88a) — shapes below are transcribed
// from that commit's actual response-builder source (bigWalkWorldResponse,
// the /progress and /save-history handlers in src/api.ts), NOT from a live
// capture: the live deployment has a separate, confirmed bug where
// worldState() returns Abiotic Factor's snapshot for a server with no
// upload yet, so the only trustworthy ground truth right now is the
// merged source.

describe("LocalSaveStatusSchema", () => {
  it("parses a real /status response for a local-save server with an upload", () => {
    const body = {
      server: { id: "big_walk", displayName: "Big Walk", color: "#78dcca" },
      game: "big-walk",
      lastUploadedAt: 1786726722336,
      lastPlayedAt: 1786726700000,
      totalPlaytimeSeconds: 54000,
      activePlayers: [],
      activeProfiles: [],
    };
    expect(() => LocalSaveStatusSchema.parse(body)).not.toThrow();
  });

  it("parses before lastPlayedAt/totalPlaytimeSeconds exist (JSON drops undefined keys)", () => {
    const body = {
      server: { id: "big_walk", displayName: "Big Walk", color: "#78dcca" },
      game: "big-walk",
      lastUploadedAt: 1786726722336,
      activePlayers: [],
      activeProfiles: [],
    };
    expect(() => LocalSaveStatusSchema.parse(body)).not.toThrow();
  });
});

describe("BigWalkWorldSchema", () => {
  it("parses a real /world response (bigWalkWorldResponse's own shape)", () => {
    const body = {
      game: "big-walk",
      savedAt: 1786726722336,
      slotName: "save_zone_1",
      filenameUid: "abc123",
      worldSeed: 42,
      lastPlayedAt: 1786726700000,
      totalPlaytimeSeconds: 54000,
      skipAidsActive: false,
      players: [
        { steamId: "76561190000000001", head: 3, torso: 1, legs: 2, walkingProven: true },
      ],
      completions: {
        gourds: { gourd_1: 1 },
        bigKeys: { bigKey_1: 1 },
        fmStations: {},
        lookoutLights: {},
        namedDoors: { SpawnHubGate: 1 },
      },
      uuidFlags: { "0000aaaa-1111-2222-3333-444455556666": 1 },
      notes: { note_1: "a warning scrawled on the wall" },
      inventory: ["item_flashlight", "item_map"],
    };
    expect(() => BigWalkWorldSchema.parse(body)).not.toThrow();
  });
});

describe("GameProgressSchema", () => {
  it("parses a real /progress response", () => {
    const body = {
      game: "big-walk",
      slotName: "save_zone_1",
      lastPlayedAt: 1786726700000,
      totalPlaytimeSeconds: 54000,
      totalPlaytimeHours: 15,
      playerCount: 2,
      inventoryCount: 8,
      completionCounts: {
        gourds: 3, bigKeys: 1, fmStations: 0, lookoutLights: 2, namedDoors: 1, uuidFlags: 4,
      },
    };
    expect(() => GameProgressSchema.parse(body)).not.toThrow();
  });
});

describe("SaveHistorySchema", () => {
  it("parses a real /save-history response", () => {
    const body = {
      hours: 168, from: 1786470056152, to: 1787074856152,
      data: [
        {
          uploadedAt: 1786726722336, hash: "abc123def456",
          totalPlaytimeSeconds: 54000, completedGourds: 3, completedBigKeys: 1,
          completedFmStations: 0, completedLookoutLights: 2, completedNamedDoors: 1,
          completedUuidFlags: 4, playerCount: 2, inventoryCount: 8,
        },
      ],
    };
    expect(() => SaveHistorySchema.parse(body)).not.toThrow();
  });

  it("parses an empty history before any upload", () => {
    const body = { hours: 168, from: 0, to: 1, data: [] };
    expect(() => SaveHistorySchema.parse(body)).not.toThrow();
  });
});
