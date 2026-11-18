import "server-only";
import { z } from "zod";
import { authenticationAssertion, managedSessionSchema, managementURL,
  type ManagedSession } from "./management-session";

const statuses = { complete: 200, denied: 403, unavailable: 503, incomplete: 409,
  invalid_request: 400 } as const;
const reply = z.object({ status: z.enum(["complete", "denied", "unavailable", "incomplete",
  "invalid_request"]), detail: z.string().optional() }).strict();
const privateHeaders = { "Cache-Control": "private, no-store" };

/** Only the server's verified Auth.js session supplies the authentication event. */
export async function submitManagement(session: ManagedSession, body: string): Promise<Response> {
  const retained = managedSessionSchema.safeParse(session);
  if (!retained.success) return Response.json({ status: "denied" },
    { status: 401, headers: privateHeaders });
  const path = "/app/v1/management/operations";
  try {
    const assertion = await authenticationAssertion(retained.data.authentication,
      { purpose: "session", path, method: "POST", body, binding: retained.data.binding });
    const response = await fetch(managementURL(path), {
      method: "POST", body, redirect: "error", cache: "no-store",
      signal: AbortSignal.timeout(15_000),
      headers: { Authorization: `Nora-Authentication ${assertion}`, "Content-Type": "application/json" },
    });
    const parsed = reply.safeParse(await response.json());
    if (parsed.success && statuses[parsed.data.status] === response.status) {
      return Response.json(parsed.data, { status: response.status, headers: privateHeaders });
    }
    if (response.status === 401 && parsed.success && parsed.data.status === "denied") {
      return Response.json({ status: "denied" }, { status: 401, headers: privateHeaders });
    }
  } catch {
    // Failure to check or publish is never a successful management response.
  }
  return Response.json({ status: "unavailable" }, { status: 503, headers: privateHeaders });
}
