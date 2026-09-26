import { describe, expect, it } from "vitest";
import { ROUTES, resolveUpstream } from "./upstream";

const q = (s = "") => new URLSearchParams(s);
const ok = (segments: string[], search = "", viewer = "miles") => {
  const r = resolveUpstream(segments, q(search), viewer);
  if (!r.ok) throw new Error(`expected ${segments.join("/")} to resolve, got ${r.status}`);
  return r.upstreamPath;
};

describe("resolveUpstream", () => {
  it("forwards a plain read unchanged", () => {
    expect(ok(["status"])).toBe("/app/v1/status");
    expect(ok(["games", "servers", "abiotic", "status"])).toBe(
      "/app/v1/games/servers/abiotic/status",
    );
  });

  it("scopes memory and events to the session's person, ignoring the browser's", () => {
    expect(ok(["memory"], "personId=ken")).toBe("/app/v1/memory?personId=miles");
    expect(ok(["events"], "personId=ken", "jo")).toBe("/app/v1/events?personId=jo");
    expect(ok(["events", "e1"], "personId=ken", "jo")).toBe("/app/v1/events/e1?personId=jo");
  });

  it("forwards only the query parameters a route names", () => {
    expect(ok(["presence", "ken", "history"], "hours=24&personId=x&evil=1")).toBe(
      "/app/v1/presence/ken/history?hours=24",
    );
    expect(ok(["games", "servers", "a", "players"], "page=2&online=true&x=1")).toBe(
      "/app/v1/games/servers/a/players?page=2&online=true",
    );
  });

  it("prefers literal segments over parameters", () => {
    expect(ok(["presence", "analytics", "places"])).toBe("/app/v1/presence/analytics/places");
  });

  it("refuses paths outside the table", () => {
    for (const segments of [
      [],
      ["health"],
      ["memory", "ken"],
      ["presence", "ken"],
      ["games", "servers", "a", "players", "b", "c"],
      ["voice", "utterance"],
    ]) {
      expect(resolveUpstream(segments, q(), "miles")).toEqual({ ok: false, status: 404 });
    }
  });

  it("refuses id segments that could walk the upstream path", () => {
    for (const bad of ["..", ".", "a/b", "a b", "%2e%2e", "a?b"]) {
      expect(resolveUpstream(["persons", bad], q(), "miles").ok).toBe(false);
    }
  });

  it("refuses query values outside the id charset", () => {
    expect(resolveUpstream(["presence", "ken", "history"], q("hours=1&x=2"), "m").ok).toBe(true);
    expect(resolveUpstream(["presence", "ken", "history"], q("hours=1%26personId%3Dx"), "m")).toEqual(
      { ok: false, status: 400 },
    );
  });

  it("names every self-scoped route explicitly", () => {
    const scoped = ROUTES.filter((r) => r.scope === "self").map((r) => r.pattern.join("/"));
    expect(scoped).toEqual(["memory", "events", "events/:id"]);
  });
});
