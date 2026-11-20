import { afterEach, describe, expect, it, vi } from "vitest";
import {
  availableVerbs,
  createLinkRequest,
  decisionTransport,
  fetchLinkRequest,
  linkRequestView,
  unlinkAccount,
  type LinkRequestView,
} from "./holder";
import { ApiError } from "./client";

// A generated view, whole — the tests bend one field at a time.
const accountA = { platform: "discord" as const, kind: "user_id", value: "generated-a" };
const accountB = { platform: "signal" as const, kind: "uuid", value: "generated-b" };
const view: LinkRequestView = {
  request_id: "generated-request", side: "initiating", state: "pending",
  pair: { initiating: accountA, receiving: accountB },
  surviving_person: "generated-person",
  created_at: "2026-11-01T00:00:00+00:00", expires_at: "2099-11-01T00:15:00+00:00",
  approved: [], challenge: { nonce: "generated-nonce", approve: "a", refuse: "r" },
};

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("the verbs a side may take", () => {
  it("offers the initiating side approve+cancel, the receiving side approve+refuse", () => {
    expect(availableVerbs(view)).toEqual(["approve", "cancel"]);
    expect(availableVerbs({ ...view, side: "receiving" })).toEqual(["approve", "refuse"]);
  });

  it("offers nothing once decided, answered or out of time", () => {
    for (const stale of [
      { ...view, state: "committed" as const },
      { ...view, state: "refused" as const },
      { ...view, state: "cancelled" as const },
      { ...view, state: "expired" as const },
      { ...view, state: "committing" as const },
      { ...view, approved: ["initiating" as const] },
      { ...view, expires_at: "2001-11-01T00:15:00+00:00" },
    ]) {
      expect(availableVerbs(stale), JSON.stringify(stale.state)).toEqual([]);
    }
    // A past expiry the clock alone sees — no server state needed.
    expect(availableVerbs({ ...view, expires_at: "2001-01-01T00:00:00+00:00" },
      Date.parse("2026-11-01T00:00:00+00:00"))).toEqual([]);
    // And the same clock leaves a live request live.
    expect(availableVerbs(view, Date.parse("2026-11-01T00:00:00+00:00")))
      .toEqual(["approve", "cancel"]);
  });
});

describe("the decision's transport", () => {
  it("is the provider only for Discord/Telegram; Signal decides by its own message", () => {
    expect(decisionTransport(view)).toBe("provider");
    expect(decisionTransport({ ...view, pair: { ...view.pair, initiating: accountB } }))
      .toBe("signal");
  });
});

describe("the holder envelope", () => {
  it("returns the ok view, and each refusal under its own name", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      expect(path).toBe("/api/v1/holder/link-requests/generated-request");
      return respond(200, { status: "ok", request: view });
    }));
    const ok = await fetchLinkRequest("generated-request");
    expect(ok).toEqual({ status: "ok", request: view });

    for (const [status, body, expected] of [
      [403, { status: "denied" }, "denied"],
      [409, { status: "incomplete" }, "incomplete"],
      [503, { status: "unavailable" }, "unavailable"],
      [200, { status: "ok" }, "unavailable"],           // ok with no view is a gap
      [200, { status: "ok", request: { ...view, pair: {} } }, "unavailable"],
      [200, "not-json-at-all", "unavailable"],
      [500, { status: "denied", note: "wrong-shape" }, "unavailable"],
    ] as const) {
      vi.stubGlobal("fetch", vi.fn(async () => respond(status, body)));
      expect(await fetchLinkRequest("generated-request")).toEqual({ status: expected });
    }
  });

  it("treats a dead transport as unavailable and a bare 401 as signed out", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    expect(await fetchLinkRequest("generated-request")).toEqual({ status: "unavailable" });
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response("no", { status: 401 })));
    await expect(fetchLinkRequest("generated-request"))
      .rejects.toBeInstanceOf(ApiError);
  });

  it("posts the exact create and unlink bodies", async () => {
    const seen: { path: string; method: string; body: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit) => {
      seen.push({ path, method: init.method ?? "GET", body: String(init.body) });
      return path.endsWith("/unlink")
        ? respond(200, { status: "ok" })
        : respond(200, { status: "ok", request: view });
    }));
    const created = await createLinkRequest({
      request_id: "generated-request",
      receiving: { platform: "signal", kind: "uuid", value: "generated-b" },
      surviving_person: "generated-person",
    });
    expect(created).toEqual({ status: "ok", request: view });
    expect(seen[0]).toEqual({
      path: "/api/v1/holder/link-requests", method: "POST",
      body: JSON.stringify({ request_id: "generated-request",
        receiving: { platform: "signal", kind: "uuid", value: "generated-b" },
        surviving_person: "generated-person" }),
    });
    const removed = await unlinkAccount({
      operation_id: "generated-operation", expected_person: "generated-person" });
    expect(removed).toEqual({ status: "ok" });
    expect(seen[1]).toEqual({
      path: "/api/v1/holder/unlink", method: "POST",
      body: JSON.stringify({ operation_id: "generated-operation",
        expected_person: "generated-person" }),
    });
  });

  it("keeps the strict view shape — a smuggled field never parses", () => {
    expect(linkRequestView.safeParse(view).success).toBe(true);
    expect(linkRequestView.safeParse({ ...view, approved_by: "someone" }).success).toBe(false);
    expect(linkRequestView.safeParse({ ...view, challenge: { nonce: "x" } }).success).toBe(false);
  });
});
