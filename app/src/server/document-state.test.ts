import { describe, expect, it } from "vitest";
import { proxyDocumentState } from "./document-state";

const ID = "0b9d9f6e-3c0a-4f57-9a43-0d6f3a1c2e11";

type Seen = { url?: string; init?: RequestInit; calls: number };
function upstream(init: { status?: number; body?: string } = {}, seen: Seen = { calls: 0 }) {
  return (async (url: string | URL | Request, req?: RequestInit) => {
    seen.url = String(url);
    seen.init = req;
    seen.calls += 1;
    return new Response(init.body ?? JSON.stringify({ id: ID, state: "archived" }), {
      status: init.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

function request(
  body: unknown = { state: "archived" },
  headers: Record<string, string> = { origin: "https://nora.example", host: "nora.example" },
  contentType = "application/json",
) {
  return new Request("https://nora.example/api/v1/documents/x/state", {
    method: "POST",
    headers: { ...headers, "content-type": contentType },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const call = (
  over: Partial<Parameters<typeof proxyDocumentState>[0]> = {},
  fetchImpl = upstream(),
) =>
  proxyDocumentState({
    base: "http://nora:9100",
    personId: "miles",
    id: ID,
    request: request(),
    fetchImpl,
    ...over,
  });

describe("proxyDocumentState", () => {
  it("posts the state for the SESSION's person, on a path built only from a UUID", async () => {
    const seen: Seen = { calls: 0 };
    const res = await call({ personId: "jo" }, upstream({}, seen));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ id: ID, state: "archived" });
    expect(seen.url).toBe(`http://nora:9100/app/v1/documents/${ID}/state?personId=jo`);
    expect(seen.init?.method).toBe("POST");
    expect(seen.init?.body).toBe(JSON.stringify({ state: "archived" }));
  });

  it("refuses a write from another origin, or with no origin, before any request", async () => {
    for (const headers of [
      { origin: "https://evil.example", host: "nora.example" } as Record<string, string>,
      { host: "nora.example" },
      { origin: "null", host: "nora.example" },
    ]) {
      const seen: Seen = { calls: 0 };
      const res = await call({ request: request(undefined, headers) }, upstream({}, seen));
      expect(res.status).toBe(403);
      expect(seen.calls).toBe(0);
    }
  });

  it("accepts the forwarded host a tunnel sets", async () => {
    const res = await call({
      request: request(undefined, {
        origin: "https://nora.example",
        host: "app:3400",
        "x-forwarded-host": "nora.example",
      }),
    });
    expect(res.status).toBe(200);
  });

  it("refuses a body that is not JSON, a state that is not one of three, and a bad id", async () => {
    const seen: Seen = { calls: 0 };
    const f = upstream({}, seen);
    expect((await call({ request: request({ state: "archived" }, undefined, "text/plain") }, f)).status).toBe(415);
    expect((await call({ request: request("not json") }, f)).status).toBe(400);
    for (const state of ["purged", "", null, 3, "ARCHIVED"]) {
      expect((await call({ request: request({ state }) }, f)).status).toBe(400);
    }
    expect((await call({ id: "../../status" }, f)).status).toBe(404);
    expect((await call({ id: "5b6f0c3a2d41" }, f)).status).toBe(404); // a handle is not an id here
    expect(seen.calls).toBe(0);
  });

  it("answers Nora's not-found as 404 and anything else as unavailable, never as done", async () => {
    expect((await call({}, upstream({ status: 404, body: "{}" }))).status).toBe(404);
    expect((await call({}, upstream({ status: 400, body: "{}" }))).status).toBe(503);
    expect((await call({}, upstream({ status: 503, body: "{}" }))).status).toBe(503);
    expect((await call({}, upstream({ body: "<html>" }))).status).toBe(502);
    const down = (async () => {
      throw new Error("down");
    }) as typeof fetch;
    expect((await call({}, down)).status).toBe(503);
  });
});
