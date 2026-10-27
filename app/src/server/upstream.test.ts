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

  it("forwards a documents search in any script, re-encoded, and refuses a control character or an over-long one", () => {
    // Spaces, Chinese and punctuation are a search, not an id: they must survive the proxy.
    const sp = new URLSearchParams({ q: "里斯本 trip & 100%", state: "archived" });
    const r = resolveUpstream(["documents"], sp, "jo");
    if (!r.ok) throw new Error("a search must resolve");
    const forwarded = new URL(`http://x${r.upstreamPath}`).searchParams;
    expect(forwarded.get("q")).toBe("里斯本 trip & 100%");
    expect(forwarded.get("personId")).toBe("jo");
    expect(forwarded.get("state")).toBe("archived");
    expect(resolveUpstream(["documents"], new URLSearchParams({ q: "a\u0000b" }), "jo")).toEqual({
      ok: false,
      status: 400,
    });
    expect(resolveUpstream(["documents"], new URLSearchParams({ q: "x".repeat(201) }), "jo")).toEqual({
      ok: false,
      status: 400,
    });
    expect(ok(["documents"], new URLSearchParams({ q: "x".repeat(200) }).toString(), "jo")).toContain("q=");
    // Only the documents list takes it: another route still drops an unnamed parameter.
    expect(ok(["persons"], "q=anything")).toBe("/app/v1/persons");
  });

  it("answers a plan id that cannot be one as not found, before asking Nora", () => {
    // Nora answers a non-UUID id with 400, which the portal would show as
    // "couldn't check"; there is nothing to check.
    expect(resolveUpstream(["events", "not-a-real-plan"], q(), "miles")).toEqual({ ok: false, status: 404 });
    expect(resolveUpstream(["events", "9c48dbf17c25446784b3ac9d8d61a584"], q(), "miles").ok).toBe(true);
  });

  it("forwards only the query parameters a route names", () => {
    expect(ok(["presence", "ken", "history"], "hours=24&personId=x&evil=1", "jo")).toBe(
      "/app/v1/presence/ken/history?hours=24&personId=jo",
    );
    expect(ok(["games", "servers", "a", "players"], "page=2&online=true&x=1")).toBe(
      "/app/v1/games/servers/a/players?page=2&online=true",
    );
  });

  it("prefers literal segments over parameters", () => {
    expect(ok(["presence", "analytics", "places"], "", "jo")).toBe(
      "/app/v1/presence/analytics/places?personId=jo",
    );
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
    expect(scoped).toEqual([
      "memory",
      "events",
      "events/:id",
      "documents",
      "documents/:id",
      "documents/:id/revisions",
      "documents/:id/revisions/:n",
      "artifacts",
      "artifacts/:id",
      "presence",
      "presence/places",
      "presence/analytics/places",
      "presence/analytics/rhythm",
      "presence/analytics/routes",
      "presence/:personId/history",
    ]);
  });

  it("scopes artifacts to the session's person and lets only a link id or a UUID through", () => {
    const link = "a1b2c3d4e5f6";
    const chat = "0b9d9f6e-3c0a-4f57-9a43-0d6f3a1c2e11";
    expect(ok(["artifacts"], "personId=ken&page=2&q=trip&evil=1", "jo")).toBe(
      "/app/v1/artifacts?page=2&q=trip&personId=jo",
    );
    expect(ok(["artifacts", link], "personId=ken", "jo")).toBe(`/app/v1/artifacts/${link}?personId=jo`);
    expect(ok(["artifacts", chat], "", "jo")).toBe(`/app/v1/artifacts/${chat}?personId=jo`);
    for (const bad of ["A1B2C3D4E5F6", "a1b2c3d4e5f", "..", "keep"]) {
      expect(resolveUpstream(["artifacts", bad], q(), "jo"), bad).toEqual({ ok: false, status: 404 });
    }
    // The bytes are never read through the JSON proxy: they have their own route.
    expect(resolveUpstream(["artifacts", link, "download"], q(), "jo")).toEqual({ ok: false, status: 404 });
  });
});
