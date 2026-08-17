import { describe, expect, it } from "vitest";
import { GraphSchema, ServerSchema, ServerStatusSchema } from "./games-schema";

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
