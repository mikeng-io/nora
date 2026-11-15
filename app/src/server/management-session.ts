import "server-only";
import { createHash, randomUUID } from "node:crypto";
import { SignJWT } from "jose";
import { z } from "zod";

const revision = z.string().regex(/^(0|[1-9][0-9]*)$/);
const bindingSchema = z.object({
  person_id: z.string().min(1), person_revision: revision,
  account_id: z.string().min(1), account_revision: revision,
}).strict();
const authenticationSchema = z.object({
  sessionId: z.string().min(1),
  account: z.object({ platform: z.enum(["discord", "telegram", "signal", "whatsapp"]),
    kind: z.string().min(1), value: z.string().min(1) }).strict(),
  method: z.enum(["discord_oauth", "telegram_oidc", "signal_code", "whatsapp_code"]),
  proofIssuer: z.string().min(1), authenticatedAt: z.number().int().nonnegative(),
}).strict();
export const managedSessionSchema = z.object({
  authentication: authenticationSchema, binding: bindingSchema,
}).strict();
export type AuthenticationEvent = z.infer<typeof authenticationSchema>;
export type ManagedSession = z.infer<typeof managedSessionSchema>;
type Reply = { status: "ok"; binding: ManagedSession["binding"]; displayName: string }
  | { status: "denied" } | { status: "unavailable" };

export function identityBackend(): "file" | "managed" {
  const value = process.env.NORA_IDENTITY_BACKEND ?? "file";
  if (value !== "file" && value !== "managed") throw new Error("Invalid identity backend");
  return value;
}

/** Sign only the authentication retained by Auth.js, never browser/model actor fields. */
export async function authenticationAssertion(
  authentication: AuthenticationEvent,
  request: { purpose: "sign_in" | "session"; path: string; method: string; body: string;
    binding?: ManagedSession["binding"] },
): Promise<string> {
  const event = authenticationSchema.parse(authentication);
  const issuer = process.env.NORA_AUTHENTICATION_ISSUER ?? "portal";
  const secret = process.env.NORA_AUTHENTICATION_KEY ?? "";
  const key = new TextEncoder().encode(secret);
  if (!issuer.trim() || key.length < 32 || secret === process.env.AUTH_SECRET
    || secret === process.env.NORA_OTP_API_TOKEN) {
    throw new Error("Authentication issuer is unavailable");
  }
  if ((request.purpose === "session") !== (request.binding !== undefined)) {
    throw new Error("A retained session requires its original binding");
  }
  const now = Math.floor(Date.now() / 1000);
  return new SignJWT({
    account: event.account, authentication_method: event.method,
    proof_issuer: event.proofIssuer, authenticated_at: event.authenticatedAt,
    purpose: request.purpose,
    ...(request.binding ? { binding: bindingSchema.parse(request.binding) } : {}),
    method: request.method, path: request.path,
    body_sha256: createHash("sha256").update(request.body).digest("hex"),
  }).setProtectedHeader({ alg: "HS256", typ: "nora.authn+jwt", kid: issuer })
    .setIssuer(issuer).setAudience("nora-managed-api").setSubject(event.sessionId)
    .setJti(randomUUID()).setIssuedAt(now).setExpirationTime(now + 60).sign(key);
}

async function call(
  purpose: "sign_in" | "session", authentication: AuthenticationEvent,
  binding?: ManagedSession["binding"],
): Promise<Reply> {
  const path = `/app/v1/management/auth/${purpose === "sign_in" ? "exchange" : "check"}`;
  const body = "{}";
  try {
    const assertion = await authenticationAssertion(authentication,
      { purpose, path, body, method: "POST", binding });
    const base = new URL(process.env.NORA_API_BASE ?? "http://nora:9100");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password
      || base.search || base.hash) return { status: "unavailable" };
    const response = await fetch(`${base.href.replace(/\/$/, "")}${path}`, {
      method: "POST", body, cache: "no-store", redirect: "error",
      headers: { Authorization: `Nora-Authentication ${assertion}`, "Content-Type": "application/json" },
      signal: AbortSignal.timeout(10_000),
    });
    const data: unknown = await response.json();
    if (response.status === 200) {
      const result = z.object({ status: z.literal("ok"), binding: bindingSchema,
        display_name: z.string() }).strict().safeParse(data);
      return result.success
        ? { status: "ok", binding: result.data.binding, displayName: result.data.display_name }
        : { status: "unavailable" };
    }
    if ((response.status === 401 || response.status === 403)
      && z.object({ status: z.literal("denied") }).strict().safeParse(data).success) {
      return { status: "denied" };
    }
  } catch {
    return { status: "unavailable" };
  }
  return { status: "unavailable" };
}

export async function captureManagedSession(authentication: AuthenticationEvent): Promise<ManagedSession> {
  const response = await call("sign_in", authentication);
  if (response.status !== "ok") throw new Error("Managed identity authentication failed");
  return { authentication, binding: response.binding };
}

export async function checkManagedSession(session: ManagedSession): Promise<Reply> {
  const parsed = managedSessionSchema.safeParse(session);
  if (!parsed.success) return { status: "denied" };
  return call("session", parsed.data.authentication, parsed.data.binding);
}
