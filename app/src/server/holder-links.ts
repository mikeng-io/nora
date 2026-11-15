import "server-only";
import { z } from "zod";
import { auth } from "@/auth";
import { identityBackend, managedSessionSchema, managementURL } from "./management-session";
import { privateApiFetch } from "./private-api-fetch";

const privateHeaders = { "Cache-Control": "private, no-store" };
const account = z.object({ platform: z.enum(["discord", "signal", "telegram"]),
  kind: z.string().min(1), value: z.string().min(1) }).strict();
/** The private request view shape — shared with the consent unit, which must
 * bind the same server-read request rather than re-parse it differently. */
export const linkRequestView = z.object({
  request_id: z.string().min(1), side: z.enum(["initiating", "receiving"]),
  state: z.enum(["pending", "committing", "committed", "refused", "cancelled", "expired"]),
  pair: z.object({ initiating: account, receiving: account }).strict(),
  surviving_person: z.string().min(1), created_at: z.iso.datetime({ offset: true }),
  expires_at: z.iso.datetime({ offset: true }),
  approved: z.array(z.enum(["initiating", "receiving"])),
  challenge: z.object({ nonce: z.string().min(1), approve: z.string(), refuse: z.string() }).strict(),
}).strict();
const failure = z.object({ status: z.enum(["denied", "unavailable", "incomplete"]),
  detail: z.string().optional() }).strict();
const response = (status: string, code: number) => Response.json({ status },
  { status: code, headers: privateHeaders });

type Context = { params: Promise<{ path: string[] }> };

/** The session route admits holder reads/ownership removal, never bilateral consent. */
export async function holderRoute(request: Request, context: Context): Promise<Response> {
  try {
    const { path } = await context.params;
    const create = request.method === "POST" && path.length === 1 && path[0] === "link-requests";
    const unlink = request.method === "POST" && path.length === 1 && path[0] === "unlink";
    const read = request.method === "GET" && path.length === 2 && path[0] === "link-requests"
      && /^[A-Za-z0-9_-]{1,128}$/.test(path[1]);
    if (!create && !unlink && !read) return response("denied", 404);
    if (new URL(request.url).search) return response("denied", 400);
    if (request.method === "POST"
      && request.headers.get("Origin") !== new URL(process.env.AUTH_URL ?? request.url).origin) {
      return response("denied", 403);
    }
    if (identityBackend() !== "managed") return response("denied", 403);
    const session = await auth();
    const retained = managedSessionSchema.safeParse(session?.user?.managedIdentity);
    if (!retained.success) {
      return response("denied", 401);
    }
    const upstream = `/app/v1/holder/${path.map(encodeURIComponent).join("/")}`;
    const body = request.method === "POST" ? await request.text() : undefined;
    const result = await privateApiFetch(managementURL(upstream), {
      method: request.method, body, signal: AbortSignal.timeout(15_000),
      headers: { "Content-Type": "application/json" },
    });
    const json: unknown = await result.json();
    const success = z.object({ status: z.literal("ok"), request: linkRequestView.optional() }).strict().safeParse(json);
    if (result.status === 200 && success.success) {
      const requestView = success.data.request;
      if (unlink ? requestView !== undefined : requestView === undefined) return response("unavailable", 503);
      if (read && requestView?.request_id !== path[1]) return response("unavailable", 503);
      if (requestView) {
        const own = requestView.pair[requestView.side];
        const account = retained.data.authentication.account;
        if (own.platform !== account.platform || own.kind !== account.kind || own.value !== account.value) {
          return response("unavailable", 503);
        }
      }
      return Response.json(success.data, { headers: privateHeaders });
    }
    const refused = failure.safeParse(json);
    if (refused.success && (([400, 401, 403].includes(result.status) && refused.data.status === "denied")
      || (result.status === 409 && refused.data.status === "incomplete")
      || (result.status === 503 && refused.data.status === "unavailable"))) {
      return Response.json(refused.data, { status: result.status, headers: privateHeaders });
    }
  } catch {
    // A failed identity/response check never becomes success or an empty request.
  }
  return response("unavailable", 503);
}
