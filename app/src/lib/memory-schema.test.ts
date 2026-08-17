import { describe, expect, it } from "vitest";
import { MemorySchema } from "./memory-schema";

describe("MemorySchema", () => {
  it("parses a real /app/v1/memory response shape", () => {
    const body = [
      { person_id: "miles", display_name: "Miles", reachable: true, conclusions: ["likes climbing"] },
      { person_id: "ada", display_name: "Ada", reachable: false, conclusions: [] },
    ];
    expect(() => MemorySchema.parse(body)).not.toThrow();
  });

  it("rejects an entry missing display_name", () => {
    expect(() =>
      MemorySchema.parse([{ person_id: "miles", reachable: true, conclusions: [] }]),
    ).toThrow();
  });
});
