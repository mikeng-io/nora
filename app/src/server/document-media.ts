// One picture of one document, from Nora to the browser (ADR 0020, design §8).
//
// The catch-all proxy (src/app/api/v1/[...path]/route.ts) answers JSON only, so the
// bytes have their own route, and this is its logic, kept apart so it is tested
// without a server. Rules, each tested in document-media.test.ts:
//   - the id must be a UUID and the picture a full lower-case sha256 before any
//     request is made, so neither can steer the upstream path;
//   - `personId` is the SESSION's, never the browser's;
//   - only a raster picture is ever passed on (png, jpeg, gif, webp): Nora already
//     reads the type off the bytes, and this refuses an upstream that says anything
//     else, so a wrong answer upstream still cannot put script on this origin;
//   - the response is labelled `nosniff` and `Content-Security-Policy: sandbox`;
//   - Nora's "not found" (unreadable, not named, gone, not a picture) is one 404 here,
//     and "could not check" is a 503, never a 404. A 400 from Nora cannot be a bad id
//     (checked above), so it is the two sides disagreeing about who the viewer is: 503.

export const DOCUMENT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const MEDIA_SHA = /^[0-9a-f]{64}$/;
export const RASTER_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

export const PICTURE_HEADERS = {
  "x-content-type-options": "nosniff",
  "content-security-policy": "sandbox; default-src 'none'",
  // Never kept: a picture cached for five minutes outlives a sign-out on a shared browser
  // profile and a removal from a room, which Nora enforces on the next read.
  "cache-control": "private, no-store",
  "content-disposition": "inline",
} as const;

const PRIVATE = { "cache-control": "private, no-store" };
const UPSTREAM_TIMEOUT_MS = 15_000;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...PRIVATE, "content-type": "application/json" },
  });
}

export async function proxyDocumentMedia(args: {
  base: string;
  personId: string;
  id: string;
  sha: string;
  fetchImpl?: typeof fetch;
}): Promise<Response> {
  const { base, personId, id, sha } = args;
  const doFetch = args.fetchImpl ?? fetch;
  if (!DOCUMENT_ID.test(id) || !MEDIA_SHA.test(sha)) return json(404, { error: "not found" });

  const url =
    `${base}/app/v1/documents/${encodeURIComponent(id)}/media/${encodeURIComponent(sha)}` +
    `?personId=${encodeURIComponent(personId)}`;
  let res: Response;
  try {
    res = await doFetch(url, { cache: "no-store", signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS) });
  } catch {
    return json(503, { error: "unavailable" });
  }
  if (res.status === 404) {
    await res.body?.cancel();
    return json(404, { error: "not found" });
  }
  if (!res.ok) {
    await res.body?.cancel();
    return json(503, { error: "unavailable" });
  }
  const type = (res.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (!RASTER_TYPES.has(type)) {
    await res.body?.cancel();
    return json(502, { error: "unavailable" });
  }
  return new Response(res.body, {
    status: 200,
    headers: { ...PICTURE_HEADERS, "content-type": type },
  });
}
