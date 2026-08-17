import { describe, expect, it } from "vitest";
import { StatusSchema } from "./status-schema";

describe("StatusSchema", () => {
  it("parses a real /app/v1/status response shape", () => {
    const body = {
      uptime_seconds: 1234.5,
      store: { reachable: true },
      honcho: { reachable: true },
      reminders: { count: 2, next_due_at: "2026-08-18T09:00:00+00:00" },
    };
    expect(() => StatusSchema.parse(body)).not.toThrow();
  });

  it("parses next_due_at: null", () => {
    const body = {
      uptime_seconds: 0,
      store: { reachable: false },
      honcho: { reachable: false },
      reminders: { count: 0, next_due_at: null },
    };
    expect(() => StatusSchema.parse(body)).not.toThrow();
  });

  it("rejects a response missing a required field", () => {
    expect(() => StatusSchema.parse({ uptime_seconds: 0 })).toThrow();
  });
});
