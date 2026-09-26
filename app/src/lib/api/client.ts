import type { z } from "zod";

// The browser's one door into Nora. Every read goes through the same-origin
// proxy at /api/v1/* (src/app/api/v1/[...path]/route.ts), which is the only
// thing that ever reaches Nora's :9100 API, and which injects the viewer's
// own person id where a route is person-scoped. The browser never names
// Nora's host.
//
// A read ends in exactly one of four states, and the UI renders each
// differently (R-27's three states, plus the session one):
//   ok           the parsed payload
//   NotFound     404: the thing does not exist ("nothing_found")
//   Unavailable  network down, 5xx, or a body this page cannot read
//                ("unavailable": I couldn't check, never "there's nothing")
//   SignedOut    401: the session ended; the shell sends the viewer to sign in

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly kind: "not-found" | "unavailable" | "signed-out" | "bad-payload",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const isNotFound = (e: unknown) => e instanceof ApiError && e.kind === "not-found";
export const isSignedOut = (e: unknown) => e instanceof ApiError && e.kind === "signed-out";

export const API_BASE = "/api/v1";

type Query = Record<string, string | number | boolean | undefined | null>;

export function apiPath(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

export async function apiGet<T>(
  path: string,
  schema: z.ZodType<T>,
  opts: { query?: Query; signal?: AbortSignal } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiPath(path, opts.query), {
      cache: "no-store",
      credentials: "same-origin",
      headers: { accept: "application/json" },
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error)?.name === "AbortError") throw err;
    throw new ApiError(`network: ${path}`, 0, "unavailable");
  }
  if (res.status === 401) throw new ApiError(`signed out: ${path}`, 401, "signed-out");
  if (res.status === 404) throw new ApiError(`not found: ${path}`, 404, "not-found");
  if (!res.ok) throw new ApiError(`upstream ${res.status}: ${path}`, res.status, "unavailable");

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new ApiError(`unreadable body: ${path}`, res.status, "bad-payload");
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    // A shape this build does not understand is a wiring gap, not an empty
    // result: surfaced as unavailable, and logged for whoever deploys next.
    console.warn(`[nora] ${path} did not match its schema`, parsed.error.issues.slice(0, 5));
    throw new ApiError(`schema mismatch: ${path}`, res.status, "bad-payload");
  }
  return parsed.data;
}
