// Archive, delete or restore one document: a portal write to Nora (ADR 0020, design/artifact
// §5 and §34, document_state; restoring a revision is the other, document-history.ts).
//
// Its own route (src/app/api/v1/documents/[id]/state/route.ts), because the catch-all
// proxy is GET only and should stay that way. This is that route's logic, kept apart so
// it is tested without a server. Rules, each tested in document-state.test.ts:
//   - a write must come from this origin: the browser's `Origin` must name the host the
//     request came to, and the body must be JSON (a cross-site form can send neither);
//   - the id must be a UUID and the state one of three words before any request is
//     made, so neither can steer the upstream path or body;
//   - `personId` is the SESSION's, never the browser's;
//   - Nora's "not found" (no such document, not the viewer's, deleted) is one 404 here,
//     a refusal is a 400, and "could not check" is a 503, never a 404.

import { DOCUMENT_ID } from "./document-media";

export const DOCUMENT_STATES = ["active", "archived", "deleted"] as const;
export type DocumentState = (typeof DOCUMENT_STATES)[number];

const PRIVATE = { "cache-control": "private, no-store" };
const UPSTREAM_TIMEOUT_MS = 15_000;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...PRIVATE, "content-type": "application/json" },
  });
}

/** Whether a request that changes something came from a page on this origin. */
export function sameOrigin(headers: Headers): boolean {
  const origin = headers.get("origin");
  const host = headers.get("x-forwarded-host") ?? headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host.split(",")[0].trim();
  } catch {
    return false;
  }
}

export function isDocumentState(value: unknown): value is DocumentState {
  return typeof value === "string" && (DOCUMENT_STATES as readonly string[]).includes(value);
}

export async function proxyDocumentState(args: {
  base: string;
  personId: string;
  id: string;
  request: Request;
  fetchImpl?: typeof fetch;
}): Promise<Response> {
  const { base, personId, id, request } = args;
  const doFetch = args.fetchImpl ?? fetch;
  if (!sameOrigin(request.headers)) return json(403, { error: "forbidden" });
  const type = (request.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") return json(415, { error: "unsupported" });
  if (!DOCUMENT_ID.test(id)) return json(404, { error: "not found" });
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "bad body" });
  }
  const state = (body as { state?: unknown } | null)?.state;
  if (!isDocumentState(state)) return json(400, { error: "bad state" });

  const url =
    `${base}/app/v1/documents/${encodeURIComponent(id)}/state` +
    `?personId=${encodeURIComponent(personId)}`;
  let res: Response;
  try {
    res = await doFetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ state }),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return json(503, { error: "unavailable" });
  }
  if (res.status === 404) {
    await res.body?.cancel();
    return json(404, { error: "not found" });
  }
  if (!res.ok) {
    await res.body?.cancel();
    // A 400 cannot be our id or state (both checked above): the two sides disagree.
    return json(503, { error: "unavailable" });
  }
  const text = await res.text();
  try {
    return json(200, JSON.parse(text));
  } catch {
    return json(502, { error: "unavailable" });
  }
}
