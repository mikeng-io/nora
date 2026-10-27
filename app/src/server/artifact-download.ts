// One Artifacts file, from Nora to the browser (ADR 0023 decision 3).
//
// The catch-all proxy (src/app/api/v1/[...path]/route.ts) answers JSON only, so the
// bytes have their own route, and this is its logic, kept apart so it is tested without
// a server. This origin holds the session cookie, so the rules are strict, and they are
// applied HERE as well as in Nora, so a wrong answer upstream still cannot put a script
// on this origin. Each is tested in artifact-download.test.ts:
//   - the ref must be a link's 12-hex id or an attachment's UUID before any request is
//     made, so it cannot steer the upstream path;
//   - `personId` is the SESSION's, never the browser's;
//   - only a raster picture, audio or video is passed on `inline`; everything else, SVG,
//     PDF and HTML included, goes out as an `attachment`, whatever upstream said;
//   - every response carries `nosniff`, `Content-Security-Policy: sandbox` and `no-store`;
//   - the file name is kept only when it is plain ASCII with no quote, else "file";
//   - Nora's "not found" (not visible, not there) is one 404 here, and "could not check"
//     is a 503, never a 404. A 400 from Nora cannot be a bad ref (checked above), so it
//     is the two sides disagreeing about who the viewer is: 503.

import { ARTIFACT_REF } from "./upstream";

const INLINE_IMAGES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const SAFE_NAME = /^[A-Za-z0-9._-]{1,160}$/;
const UPSTREAM_TIMEOUT_MS = 30_000;
const PRIVATE = { "cache-control": "private, no-store" };

export function mayDisplayInline(type: string): boolean {
  return INLINE_IMAGES.has(type) || type.startsWith("audio/") || type.startsWith("video/");
}

/** The file name upstream gave, when it is one this route will repeat; else "file". */
export function fileNameOf(disposition: string | null): string {
  const m = /filename="([^"]*)"/.exec(disposition ?? "");
  const name = m?.[1] ?? "";
  return SAFE_NAME.test(name) && !/^\.+$/.test(name) ? name : "file";
}

export function fileHeaders(type: string, name: string, utf8Name?: string): Record<string, string> {
  const inline = mayDisplayInline(type);
  return {
    "content-type": type || "application/octet-stream",
    "content-disposition":
      `${inline ? "inline" : "attachment"}; filename="${name}"` +
      (utf8Name ? `; filename*=UTF-8''${rfc5987(utf8Name)}` : ""),
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox; default-src 'none'",
    ...PRIVATE,
  };
}

/** RFC 5987 attr-char: percent-encode everything outside the unreserved set so
 * a UTF-8 name rides the header legally (encodeURIComponent leaves '()*). */
const rfc5987 = (s: string) =>
  encodeURIComponent(s).replace(/['()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...PRIVATE, "content-type": "application/json" },
  });
}

const BYTE_RANGE = /^bytes=\d*-\d*$/;
const CONTENT_RANGE = /^bytes (\d+-\d+|\*)\/\d+$/;

export async function proxyArtifactDownload(args: {
  base: string;
  personId: string;
  id: string;
  range?: string | null;
  fetchImpl?: typeof fetch;
}): Promise<Response> {
  const { base, personId, id } = args;
  // One byte range, passed on only in its plain form: Safari plays a video only after a
  // 206 answers `Range: bytes=0-1`, and seeking needs ranges everywhere.
  const range = args.range && BYTE_RANGE.test(args.range) ? args.range : null;
  const doFetch = args.fetchImpl ?? fetch;
  if (!ARTIFACT_REF.test(id)) return json(404, { error: "not found" });

  const url =
    `${base}/app/v1/artifacts/${encodeURIComponent(id)}/download` +
    `?personId=${encodeURIComponent(personId)}`;
  let res: Response;
  try {
    res = await doFetch(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      ...(range ? { headers: { range } } : {}),
    });
  } catch {
    return json(503, { error: "unavailable" });
  }
  if (res.status === 404) {
    await res.body?.cancel();
    return json(404, { error: "not found" });
  }
  if (res.status === 416) {
    await res.body?.cancel();
    const cr = res.headers.get("content-range") ?? "";
    return new Response(null, {
      status: 416,
      headers: { ...PRIVATE, ...(CONTENT_RANGE.test(cr) ? { "content-range": cr } : {}) },
    });
  }
  if (!res.ok) {
    await res.body?.cancel();
    return json(503, { error: "unavailable" });
  }
  const type = (res.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (type === "text/html" || type === "application/xhtml+xml") {
    // Nora never serves a page; one arriving here is a fault upstream, never passed on.
    await res.body?.cancel();
    return json(502, { error: "unavailable" });
  }
  const name = fileNameOf(res.headers.get("content-disposition"));
  const headers: Record<string, string> = { ...fileHeaders(type, name), "accept-ranges": "bytes" };
  const cr = res.headers.get("content-range") ?? "";
  const partial = res.status === 206 && CONTENT_RANGE.test(cr);
  if (partial) headers["content-range"] = cr;
  const length = res.headers.get("content-length");
  if (length && /^\d+$/.test(length)) headers["content-length"] = length;
  return new Response(res.body, { status: partial ? 206 : 200, headers });
}
