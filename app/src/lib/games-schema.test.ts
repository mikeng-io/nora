import { describe, expect, it } from "vitest";
import { GraphSchema, ServerSchema, ServerStatusSchema } from "./games-schema";

// Real, observed pulse_server_status shape (nora/web/games.py's mapping),
// not adapted from Pulse's REST API — see games-schema.ts's module comment.
const CURRENT = {
  online: true,
  status: "running",
  uptimeHours: 394.3,
  cpuPercent: 47,
  memoryGb: 5.4,
  playerCount: 0,
  lastSampleAt: "2026-08-17T18:53:52.293Z",
};

describe("ServerSchema", () => {
  it("parses a real /app/v1/games/servers list entry", () => {
    const body = {
      id: "abiotic_factor",
      displayName: "Abiotic Factor",
      color: "#f59e0b",
      maxPlayers: 12,
      current: CURRENT,
    };
    expect(() => ServerSchema.parse(body)).not.toThrow();
  });

  it("allows current: null before the first poll", () => {
    const body = { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#f59e0b", current: null };
    expect(() => ServerSchema.parse(body)).not.toThrow();
  });

  it("rejects an entry missing displayName", () => {
    expect(() => ServerSchema.parse({ id: "abiotic_factor", color: "#f59e0b", current: null })).toThrow();
  });
});

describe("ServerStatusSchema", () => {
  it("parses a real /app/v1/games/servers/{id}/status response", () => {
    const body = {
      server: { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#f59e0b", maxPlayers: 12 },
      current: CURRENT,
      activePlayers: ["Miles", "Ada"],
    };
    expect(() => ServerStatusSchema.parse(body)).not.toThrow();
  });

  it("parses an empty activePlayers list", () => {
    const body = {
      server: { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#f59e0b" },
      current: CURRENT,
      activePlayers: [],
    };
    expect(() => ServerStatusSchema.parse(body)).not.toThrow();
  });

  it("rejects a response missing current", () => {
    expect(() =>
      ServerStatusSchema.parse({
        server: { id: "abiotic_factor", displayName: "Abiotic Factor", color: "#f59e0b" },
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
