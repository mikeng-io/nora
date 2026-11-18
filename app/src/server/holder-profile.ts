import "server-only";
import { z } from "zod";
import { auth } from "@/auth";
import { identityBackend, managedSessionSchema, managementURL } from "./management-session";
import { privateApiFetch } from "./private-api-fetch";

const privateHeaders = { "Cache-Control": "private, no-store" };
const failure = z.object({ status: z.enum(["denied", "unavailable", "incomplete"]),
  detail: z.string().optional() }).strict();
const response = (status: string, code: number) => Response.json({ status },
  { status: code, headers: privateHeaders });

/** The holder's own global display-name update: the request body carries only
 * operation_id and display_name — actor, target and account all derive from the
 * retained session the server signs, never from a body or model field. */
export async function POST(request: Request): Promise<Response> {
  try {
    if (new URL(request.url).search) return response("denied", 400);
    if (request.headers.get("Origin") !== new URL(process.env.AUTH_URL ?? request.url).origin) {
      return response("denied", 403);
    }
    if (identityBackend() !== "managed") return response("denied", 403);
    const session = await auth();
    const retained = managedSessionSchema.safeParse(session?.user?.managedIdentity);
    if (!retained.success) {
      return response("denied", 401);
    }
    const result = await privateApiFetch(managementURL("/app/v1/holder/profile"), {
      method: "POST", body: await request.text(), signal: AbortSignal.timeout(15_000),
      headers: { "Content-Type": "application/json" },
    });
    const json: unknown = await result.json();
    if (result.status === 200
      && z.object({ status: z.literal("ok"), detail: z.string().optional() }).strict().safeParse(json).success) {
      return Response.json({ status: "ok" }, { headers: privateHeaders });
    }
    const refused = failure.safeParse(json);
    if (refused.success && (([400, 401, 403].includes(result.status) && refused.data.status === "denied")
      || (result.status === 409 && refused.data.status === "incomplete")
      || (result.status === 503 && refused.data.status === "unavailable"))) {
      return Response.json(refused.data, { status: result.status, headers: privateHeaders });
    }
  } catch {
    // A failed identity/response check never becomes success.
  }
  return response("unavailable", 503);
}
