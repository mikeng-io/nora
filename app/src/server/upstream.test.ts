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
    const id = "9c48dbf1-7c25-4467-84b3-ac9d8d61a584";
    expect(ok(["memory"], "personId=ken")).toBe("/app/v1/memory?personId=miles");
    expect(ok(["events"], "personId=ken", "jo")).toBe("/app/v1/events?personId=jo");
    expect(ok(["events", id], "personId=ken", "jo")).toBe(`/app/v1/events/${id}?personId=jo`);
  });

  it("scopes documents to the session's person and forwards only page, pageSize and state", () => {
    expect(ok(["documents"], "state=archived&evil=1", "jo")).toBe(
      "/app/v1/documents?state=archived&personId=jo",
    );
    const id = "9c48dbf1-7c25-4467-84b3-ac9d8d61a584";
    expect(ok(["documents"], "personId=ken&page=2&pageSize=10&evil=1", "jo")).toBe(
      "/app/v1/documents?page=2&pageSize=10&personId=jo",
    );
    expect(ok(["documents", id], "personId=ken", "jo")).toBe(`/app/v1/documents/${id}?personId=jo`);
    expect(resolveUpstream(["documents", "not-a-uuid"], q(), "jo")).toEqual({ ok: false, status: 404 });
    // A link's 12-hex handle is forwarded as is; nothing else that is not an id is.
    expect(ok(["documents", "9e1d2c3b4a5f"], "personId=ken", "jo")).toBe("/app/v1/documents/9e1d2c3b4a5f?personId=jo");
    for (const bad of ["9E1D2C3B4A5F", "9e1d2c3b4a5", "9e1d2c3b4a5fa", "9e1d2c3b4a5%", "9e1d2c3b-4a5f", "../events"]) {
      expect(resolveUpstream(["documents", bad], q(), "jo"), bad).toEqual({ ok: false, status: 404 });
    }
    // The browser's own personId is never the viewer.
    expect(ok(["documents"], "personId=ken", "jo")).not.toContain("ken");
  });

  it("answers a plan id that cannot be one as not found, before asking Nora", () => {
    // Nora answers a non-UUID id with 400, which the portal would show as
    // "couldn't check"; there is nothing to check.
    expect(resolveUpstream(["events", "not-a-real-plan"], q(), "miles")).toEqual({ ok: false, status: 404 });
    expect(resolveUpstream(["events", "9c48dbf17c25446784b3ac9d8d61a584"], q(), "miles").ok).toBe(true);
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
    expect(scoped).toEqual(["memory", "events", "events/:id", "documents", "documents/:id"]);
  });
});
