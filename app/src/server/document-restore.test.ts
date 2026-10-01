import { describe, expect, it } from "vitest";
import { proxyDocumentRestore } from "./document-restore";

const ID = "0b9d9f6e-3c0a-4f57-9a43-0d6f3a1c2e11";

type Seen = { url?: string; init?: RequestInit; calls: number };
function upstream(init: { status?: number; body?: string } = {}, seen: Seen = { calls: 0 }) {
  return (async (url: string | URL | Request, req?: RequestInit) => {
    seen.url = String(url);
    seen.init = req;
    seen.calls += 1;
    return new Response(init.body ?? JSON.stringify({ status: "ok", revision: 4 }), {
      status: init.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

function request(
  body: unknown = { baseRevision: 3 },
  headers: Record<string, string> = { origin: "https://nora.example", host: "nora.example" },
  contentType = "application/json",
) {
  return new Request("https://nora.example/api/v1/documents/x/revisions/1/restore", {
    method: "POST",
    headers: { ...headers, "content-type": contentType },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const call = (over: Partial<Parameters<typeof proxyDocumentRestore>[0]> = {}, fetchImpl = upstream()) =>
  proxyDocumentRestore({
    base: "http://nora:9100",
    personId: "miles",
    id: ID,
    n: "1",
    request: request(),
    fetchImpl,
    ...over,
  });

describe("proxyDocumentRestore", () => {
  it("posts the base revision for the SESSION's person, on a path built from a UUID and a number", async () => {
    const seen: Seen = { calls: 0 };
    const res = await call({ personId: "jo" }, upstream({}, seen));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok", revision: 4 });
    expect(seen.url).toBe(`http://nora:9100/app/v1/documents/${ID}/revisions/1/restore?personId=jo`);
    expect(seen.init?.method).toBe("POST");
    expect(seen.init?.body).toBe(JSON.stringify({ baseRevision: 3 }));
  });

  it("refuses another origin, a body that is not JSON, and a bad id, revision or base, before any request", async () => {
    const seen: Seen = { calls: 0 };
    const fetchImpl = upstream({}, seen);
    expect((await call({ request: request(undefined, { origin: "https://evil.example", host: "nora.example" }) }, fetchImpl)).status).toBe(403);
    expect((await call({ request: request(undefined, undefined, "text/plain") }, fetchImpl)).status).toBe(415);
    expect((await call({ request: request("{not json") }, fetchImpl)).status).toBe(400);
    expect((await call({ id: "not-a-uuid" }, fetchImpl)).status).toBe(404);
    for (const n of ["0", "-1", "01", "1.5", "x", "../state"]) {
      expect((await call({ n }, fetchImpl)).status, n).toBe(404);
    }
    for (const baseRevision of [undefined, "3", 0, -1, 1.5, true, null]) {
      expect((await call({ request: request({ baseRevision }) }, fetchImpl)).status, String(baseRevision)).toBe(400);
    }
    expect(seen.calls).toBe(0);
  });

  it("passes on Nora's refusal word, and her not-found as one 404", async () => {
    for (const reason of ["conflict", "not_active", "invalid"]) {
      const res = await call({}, upstream({ status: 409, body: JSON.stringify({ status: reason }) }));
      expect(res.status).toBe(409);
      expect(await res.json()).toEqual({ status: reason });
    }
    const gone = await call({}, upstream({ status: 404, body: JSON.stringify({ status: "not_found" }) }));
    expect(gone.status).toBe(404);
  });

  it("never reads an outage, or a refusal it does not know, as done or as not found", async () => {
    expect((await call({}, upstream({ status: 503, body: "{}" }))).status).toBe(503);
    expect((await call({}, upstream({ status: 409, body: JSON.stringify({ status: "odd" }) }))).status).toBe(503);
    expect((await call({}, upstream({ status: 200, body: "<html>" }))).status).toBe(502);
    expect((await call({}, upstream({ status: 200, body: "{}" }))).status).toBe(502);
    const down = (async () => {
      throw new Error("down");
    }) as typeof fetch;
    expect((await call({}, down)).status).toBe(503);
  });
});
