import { describe, expect, it } from "vitest";
import { proxyDocumentMedia } from "./document-media";

const ID = "0b9d9f6e-3c0a-4f57-9a43-0d6f3a1c2e11";
const SHA = "a".repeat(64);
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);

type Seen = { url?: string };
function upstream(init: { status?: number; type?: string; body?: BodyInit } = {}, seen: Seen = {}) {
  return (async (url: string | URL | Request) => {
    seen.url = String(url);
    return new Response(init.body ?? PNG, {
      status: init.status ?? 200,
      headers: { "content-type": init.type ?? "image/png" },
    });
  }) as typeof fetch;
}
const call = (over: Partial<Parameters<typeof proxyDocumentMedia>[0]> = {}, fetchImpl = upstream()) =>
  proxyDocumentMedia({ base: "http://nora:9100", personId: "miles", id: ID, sha: SHA, fetchImpl, ...over });

describe("proxyDocumentMedia", () => {
  it("passes a raster picture through, labelled so it can never run", async () => {
    const res = await call();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/png");
    // Literal values, not the constant they come from: a test over the constant it pins cannot fail.
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-security-policy")).toContain("sandbox");
    expect(res.headers.get("cache-control")).toMatch(/^private/);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(PNG);
  });

  it("asks Nora for the SESSION's person, on a path built only from a UUID and a sha", async () => {
    const seen: Seen = {};
    await call({ personId: "jo" }, upstream({}, seen));
    expect(seen.url).toBe(`http://nora:9100/app/v1/documents/${ID}/media/${SHA}?personId=jo`);
  });

  it("makes no request for an id or a picture that could steer the path", async () => {
    let called = false;
    const spy = (async () => ((called = true), new Response(PNG))) as unknown as typeof fetch;
    for (const bad of [
      { id: "../../status" },
      { id: `${ID}?x=1` },
      { id: "not-a-uuid" },
      { sha: "a".repeat(12) },
      { sha: "A".repeat(64) },
      { sha: `${SHA}/../x` },
      { sha: "" },
    ]) {
      expect((await call(bad, spy)).status).toBe(404);
    }
    expect(called).toBe(false);
  });

  it("refuses to pass on anything that is not a raster picture, whatever upstream says", async () => {
    for (const type of ["image/svg+xml", "text/html", "application/javascript", "", "image/png2"]) {
      const res = await call({}, upstream({ type, body: "<svg/>" }));
      expect(res.status, type).toBe(502);
      expect(res.headers.get("content-type"), type).not.toMatch(/svg|html/);
    }
  });

  it("answers Nora's not-found as one 404 and 'could not check' as a 503, never the reverse", async () => {
    expect((await call({}, upstream({ status: 404, type: "application/json", body: "{}" }))).status).toBe(404);
    expect((await call({}, upstream({ status: 400, type: "application/json", body: "{}" }))).status).toBe(404);
    expect((await call({}, upstream({ status: 503, type: "application/json", body: "{}" }))).status).toBe(503);
    expect((await call({}, upstream({ status: 500, type: "application/json", body: "{}" }))).status).toBe(503);
    const down = (async () => {
      throw new Error("down");
    }) as unknown as typeof fetch;
    expect((await call({}, down)).status).toBe(503);
  });
});
