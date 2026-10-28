// The allowlist of Nora API reads the browser may make, and how each one
// reaches her :9100 API. One table instead of one route file per endpoint:
// the old portal had thirty near-identical proxies, and the two that had to
// differ (memory, events: scoped to the viewer) were each argued for in a
// comment in their own file. Here the difference is a column.
//
// Rules the table enforces, each tested in upstream.test.ts:
//   - GET only; a path not in the table is a 404, never forwarded.
//   - Only the query parameters a route names are forwarded; anything else
//     the browser sends is dropped.
//   - `scope: "self"` routes get `personId` from the SESSION, and a
//     `personId` the browser sends is ignored: memory is the viewer's own,
//     and events are the ones the viewer created or answered
//     (nora/web/memory.py, nora/web/gatherings.py); presence is only the
//     circles the viewer is in (nora/web/api.py _viewer_circles).
//   - A path parameter must look like an id (no slashes, no dots-only, a
//     bounded charset) and is re-encoded before it reaches the upstream URL.

export interface RouteSpec {
  /** Segments after /api/v1/; ":name" matches one id-shaped segment. */
  pattern: string[];
  query?: string[];
  /** Query parameters that carry a person's own words (a search): any printable text up
   * to FREE_TEXT_MAX characters instead of the id-shaped charset the others must match. */
  freeText?: string[];
  scope?: "self";
  /** A stricter shape for a named segment. A segment that fails it is a 404
   * here, the same answer the upstream gives for an id that exists and is
   * not the viewer's, rather than the upstream's 400. */
  params?: Record<string, RegExp>;
}

// An event id is a UUID (nora/web/api.py events_detail parses it with
// Python's UUID and answers 400 otherwise); anything else cannot be a plan.
const UUID = /^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$/;
// A document is opened by its id or by the 12-hex handle a link carries (the handle is not a
// UUID, so a reply may carry it); Nora resolves it among what the viewer may read.
const DOCUMENT_REF = /^(?:[0-9a-f]{12}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$/;

// An artifact is a link's 12-hex artifact_id or an attachment's UUID (nora/web/artifacts.py).
export const ARTIFACT_REF = /^(?:[0-9a-f]{12}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;

const DOCUMENT_UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const REVISION = /^[1-9][0-9]{0,8}$/;

export const ROUTES: RouteSpec[] = [
  { pattern: ["status"] },
  { pattern: ["skills"] },
  { pattern: ["tools"] },
  { pattern: ["doctrine"] },
  { pattern: ["memory"], scope: "self" },
  { pattern: ["persons"] },
  { pattern: ["persons", ":personId"] },
  { pattern: ["events"], scope: "self" },
  { pattern: ["events", ":id"], scope: "self", params: { id: UUID } },
  // Documents (ADR 0020): what the viewer created plus what was made in a room that
  // names them. `scope: "self"` because the viewer decides what is visible, never
  // the browser; Nora answers "not visible" exactly as "no such document".
  // `state` is the Archived filter (`active` or `archived`; Nora refuses anything else).
  // `q` is the search box: the current title or text, a substring, any script.
  { pattern: ["documents"], scope: "self", query: ["page", "pageSize", "state"], freeText: ["q"] },
  { pattern: ["documents", ":id"], scope: "self", params: { id: DOCUMENT_REF } },
  // A document's history (design/artifact §34): behind the document's own read gate in
  // Nora, so the same viewer rule; by its UUID only, and a revision is a positive number.
  { pattern: ["documents", ":id", "revisions"], scope: "self", params: { id: DOCUMENT_UUID } },
  {
    pattern: ["documents", ":id", "revisions", ":n"],
    scope: "self",
    params: { id: DOCUMENT_UUID, n: REVISION },
  },
  // Artifacts (ADR 0023): the files Nora sent in the rooms that name the viewer. The
  // viewer is the session's; a file they may not see is Nora's same 404 as no such file.
  // The download is its own route (src/server/artifact-download.ts): this proxy is JSON only.
  { pattern: ["artifacts"], scope: "self", query: ["page", "pageSize"], freeText: ["q"] },
  { pattern: ["artifacts", ":id"], scope: "self", params: { id: ARTIFACT_REF } },
  // Presence: only the people who share a circle with the viewer. Nora reads the
  // viewer from `personId`, so it must be the session's, never the browser's.
  { pattern: ["presence"], scope: "self" },
  { pattern: ["presence", "places"], scope: "self" },
  { pattern: ["presence", "analytics", "places"], scope: "self" },
  { pattern: ["presence", "analytics", "rhythm"], scope: "self", query: ["subjectId", "days"] },
  { pattern: ["presence", "analytics", "routes"], scope: "self", query: ["subjectId", "days"] },
  { pattern: ["presence", ":personId", "history"], scope: "self", query: ["hours"] },
  { pattern: ["games", "servers"] },
  { pattern: ["games", "servers", ":id", "status"] },
  { pattern: ["games", "servers", ":id", "graph"], query: ["hours"] },
  { pattern: ["games", "servers", ":id", "weekly"] },
  { pattern: ["games", "servers", ":id", "world"] },
  { pattern: ["games", "servers", ":id", "progress"] },
  { pattern: ["games", "servers", ":id", "save-history"], query: ["hours"] },
  { pattern: ["games", "servers", ":id", "insights"], query: ["days"] },
  { pattern: ["games", "servers", ":id", "players"], query: ["page", "pageSize", "online"] },
  { pattern: ["games", "servers", ":id", "players", ":playerKey"] },
  { pattern: ["games", "servers", ":id", "activity"], query: ["page", "pageSize"] },
  { pattern: ["games", "servers", ":id", "sessions"], query: ["page", "pageSize", "playerKey"] },
];

const ID_SEGMENT = /^[A-Za-z0-9_\-:.@]{1,128}$/;
const QUERY_VALUE = /^[A-Za-z0-9_\-:.@]{0,128}$/;
// A search is words in any script: everything but control characters, bounded as Nora's
// own limit is (`DOCUMENT_QUERY_MAX`, 200), so a longer one is a 400 here, not forwarded.
const FREE_TEXT_MAX = 200;
const hasControl = (s: string) => /[\u0000-\u001f\u007f]/.test(s);

function matches(spec: RouteSpec, segments: string[]): boolean {
  if (spec.pattern.length !== segments.length) return false;
  return spec.pattern.every((p, i) => {
    const seg = segments[i];
    if (!p.startsWith(":")) return p === seg;
    const strict = spec.params?.[p.slice(1)];
    return ID_SEGMENT.test(seg) && !/^\.+$/.test(seg) && (!strict || strict.test(seg));
  });
}

/** Literal segments beat parameters, so `presence/analytics/places` can never
 * be read as a person called "analytics". */
function specificity(spec: RouteSpec): number {
  return spec.pattern.filter((p) => !p.startsWith(":")).length;
}

export type Resolution =
  | { ok: true; upstreamPath: string; spec: RouteSpec }
  | { ok: false; status: 404 | 400 };

export function resolveUpstream(
  segments: string[],
  search: URLSearchParams,
  viewerPersonId: string,
): Resolution {
  const candidates = ROUTES.filter((spec) => matches(spec, segments)).sort(
    (a, b) => specificity(b) - specificity(a),
  );
  const spec = candidates[0];
  if (!spec) return { ok: false, status: 404 };

  const params = new URLSearchParams();
  for (const name of spec.query ?? []) {
    const value = search.get(name);
    if (value === null) continue;
    if (!QUERY_VALUE.test(value)) return { ok: false, status: 400 };
    params.set(name, value);
  }
  for (const name of spec.freeText ?? []) {
    const value = search.get(name);
    if (value === null) continue;
    if (value.length > FREE_TEXT_MAX || hasControl(value)) return { ok: false, status: 400 };
    params.set(name, value);
  }
  if (spec.scope === "self") params.set("personId", viewerPersonId);

  const path = segments.map((s) => encodeURIComponent(s)).join("/");
  const qs = params.toString();
  return { ok: true, spec, upstreamPath: `/app/v1/${path}${qs ? `?${qs}` : ""}` };
}
