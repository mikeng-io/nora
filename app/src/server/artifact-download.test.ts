import { describe, expect, it } from "vitest";
import { proxyArtifactDownload } from "./artifact-download";

const LINK = "a1b2c3d4e5f6";
const CHAT = "0b9d9f6e-3c0a-4f57-9a43-0d6f3a1c2e11";
const BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);

type Seen = { url?: string };
function upstream(
  init: { status?: number; type?: string; disposition?: string; body?: BodyInit } = {},
  seen: Seen = {},
) {
  return (async (url: string | URL | Request) => {
    seen.url = String(url);
    const headers: Record<string, string> = { "content-type": init.type ?? "image/png" };
    if (init.disposition !== undefined) headers["content-disposition"] = init.disposition;
    return new Response(init.body ?? BYTES, { status: init.status ?? 200, headers });
  }) as typeof fetch;
}
const call = (over: Partial<Parameters<typeof proxyArtifactDownload>[0]> = {}, fetchImpl = upstream()) =>
  proxyArtifactDownload({ base: "http://nora:9100", personId: "miles", id: LINK, fetchImpl, ...over });

describe("proxyArtifactDownload", () => {
  it("asks Nora for the SESSION's person, on a path built only from a checked ref", async () => {
    const seen: Seen = {};
    await call({ personId: "jo", id: CHAT }, upstream({}, seen));
    expect(seen.url).toBe(`http://nora:9100/app/v1/artifacts/${CHAT}/download?personId=jo`);
  });

  it("makes no request for a ref that could steer the path", async () => {
    let called = false;
    const spy = (async () => ((called = true), new Response(BYTES))) as unknown as typeof fetch;
    for (const id of ["../../status", `${LINK}?x=1`, "A1B2C3D4E5F6", "a1b2c3d4e5f", "", `${LINK}/x`]) {
      expect((await call({ id }, spy)).status, id).toBe(404);
    }
    expect(called).toBe(false);
  });

  it("lets only pictures, audio and video display inline; everything else downloads", async () => {
    // Literal values, not the constant they come from: a test over the constant it pins cannot fail.
    const cases: [string, string][] = [
      ["image/png", "inline"],
      ["audio/mpeg", "inline"],
      ["video/mp4", "inline"],
      ["image/svg+xml", "attachment"],
      ["application/pdf", "attachment"],
      ["text/csv", "attachment"],
      ["application/octet-stream", "attachment"],
    ];
    for (const [type, disposition] of cases) {
      // Upstream claiming `inline` changes nothing: the rule is applied here as well.
      const res = await call({}, upstream({ type, disposition: 'inline; filename="x.bin"' }));
      expect(res.status, type).toBe(200);
      expect(res.headers.get("content-disposition"), type).toBe(`${disposition}; filename="x.bin"`);
      expect(res.headers.get("x-content-type-options"), type).toBe("nosniff");
      expect(res.headers.get("content-security-policy"), type).toContain("sandbox");
      expect(res.headers.get("cache-control"), type).toBe("private, no-store");
    }
  });

  it("never passes a web page on", async () => {
    const res = await call({}, upstream({ type: "text/html", body: "<script>x</script>" }));
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).not.toMatch(/html/);
  });

  it("repeats only a plain ASCII file name", async () => {
    const named = await call({}, upstream({ disposition: 'attachment; filename="report.pdf"' }));
    expect(named.headers.get("content-disposition")).toBe('inline; filename="report.pdf"');
    for (const bad of ['attachment; filename="a\\"b.pdf"', 'attachment; filename="../x"', "attachment"]) {
      const res = await call({}, upstream({ disposition: bad }));
      expect(res.headers.get("content-disposition"), bad).toBe('inline; filename="file"');
    }
  });

  it("answers Nora's not-found as one 404 and 'could not check' as a 503, never the reverse", async () => {
    expect((await call({}, upstream({ status: 404, type: "application/json", body: "{}" }))).status).toBe(404);
    expect((await call({}, upstream({ status: 400, type: "application/json", body: "{}" }))).status).toBe(503);
    expect((await call({}, upstream({ status: 503, type: "application/json", body: "{}" }))).status).toBe(503);
    const down = (async () => {
      throw new Error("down");
    }) as unknown as typeof fetch;
    expect((await call({}, down)).status).toBe(503);
  });
  it("passes one plain byte range on and answers Nora's 206 with its Content-Range", async () => {
    // Safari plays a <video> only after a 206 answers `Range: bytes=0-1` (review of #758).
    let sent: string | null = null;
    const ranged = (async (_url: string | URL | Request, init?: RequestInit) => {
      sent = new Headers(init?.headers).get("range");
      return new Response(BYTES.slice(0, 2), {
        status: 206,
        headers: { "content-type": "video/mp4", "content-range": "bytes 0-1/4" },
      });
    }) as typeof fetch;
    const res = await call({ range: "bytes=0-1" }, ranged);
    expect(sent).toBe("bytes=0-1");
    expect(res.status).toBe(206);
    expect(res.headers.get("content-range")).toBe("bytes 0-1/4");
    expect(res.headers.get("accept-ranges")).toBe("bytes");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("drops a range header that is not one plain byte range", async () => {
    for (const range of ["bytes=0-1,3-4", "items=0-1", "bytes=0-1\r\nx: y"]) {
      let sent: string | null = "unset";
      const spy = (async (_url: string | URL | Request, init?: RequestInit) => {
        sent = new Headers(init?.headers).get("range");
        return new Response(BYTES, { headers: { "content-type": "image/png" } });
      }) as typeof fetch;
      expect((await call({ range }, spy)).status, range).toBe(200);
      expect(sent, range).toBeNull();
    }
  });

  it("answers a range past the end with a 416, never the file", async () => {
    const past = (async () =>
      new Response(null, { status: 416, headers: { "content-range": "bytes */4" } })) as typeof fetch;
    const res = await call({ range: "bytes=9-" }, past);
    expect(res.status).toBe(416);
    expect(res.headers.get("content-range")).toBe("bytes */4");
  });
});
