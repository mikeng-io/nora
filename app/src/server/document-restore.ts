// Restore an old revision of a document as a NEW revision (design/artifact §32): the
// portal's second write to Nora, beside document-state.ts and held to its rules.
//
// Its own route (src/app/api/v1/documents/[id]/revisions/[n]/restore/route.ts), because the
// catch-all proxy is GET only. Rules, each tested in document-restore.test.ts:
//   - the request must come from this origin and carry JSON (a cross-site form can send
//     neither);
//   - the id must be a UUID, the revision and `baseRevision` positive integers, before
//     any request is made, so none of them can steer the upstream path or body;
//   - `personId` is the SESSION's, never the browser's;
//   - Nora's "not found" (not the viewer's, deleted, no such revision) is one 404 here;
//     her 409 (`conflict`, `not_active`, `invalid`) is passed on with its status word,
//     because the page says which; "could not check" is a 503, never a 404.

import { DOCUMENT_ID } from "./document-media";
import { sameOrigin } from "./document-state";

const PRIVATE = { "cache-control": "private, no-store" };
const UPSTREAM_TIMEOUT_MS = 15_000;
const REVISION = /^[1-9][0-9]{0,8}$/;
const REFUSALS = new Set(["conflict", "not_active", "invalid"]);

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...PRIVATE, "content-type": "application/json" },
  });
}

const isRevision = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && REVISION.test(String(value));

export async function proxyDocumentRestore(args: {
  base: string;
  personId: string;
  id: string;
  n: string;
  request: Request;
  fetchImpl?: typeof fetch;
}): Promise<Response> {
  const { base, personId, id, n, request } = args;
  const doFetch = args.fetchImpl ?? fetch;
  if (!sameOrigin(request.headers)) return json(403, { error: "forbidden" });
  const type = (request.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") return json(415, { error: "unsupported" });
  if (!DOCUMENT_ID.test(id) || !REVISION.test(n)) return json(404, { error: "not found" });
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "bad body" });
  }
  const baseRevision = (body as { baseRevision?: unknown } | null)?.baseRevision;
  if (!isRevision(baseRevision)) return json(400, { error: "bad baseRevision" });

  const url =
    `${base}/app/v1/documents/${encodeURIComponent(id)}/revisions/${n}/restore` +
    `?personId=${encodeURIComponent(personId)}`;
  let res: Response;
  try {
    res = await doFetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ baseRevision }),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return json(503, { error: "unavailable" });
  }
  if (res.status === 404) {
    await res.body?.cancel();
    return json(404, { error: "not found" });
  }
  if (res.status === 409) {
    const refused = (await res.json().catch(() => null)) as { status?: unknown } | null;
    const reason = refused?.status;
    return typeof reason === "string" && REFUSALS.has(reason)
      ? json(409, { status: reason })
      : json(503, { error: "unavailable" });
  }
  if (!res.ok) {
    await res.body?.cancel();
    // A 400 here is the current revision (the page never offers that) or the two sides
    // disagreeing about the request: either way nothing was restored.
    return json(res.status === 400 ? 400 : 503, { error: res.status === 400 ? "refused" : "unavailable" });
  }
  const text = await res.text();
  try {
    const parsed = JSON.parse(text) as { revision?: unknown };
    if (typeof parsed.revision !== "number") return json(502, { error: "unavailable" });
    return json(200, { status: "ok", revision: parsed.revision });
  } catch {
    return json(502, { error: "unavailable" });
  }
}
