import "server-only";
import { createHash, randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { EncryptJWT, jwtDecrypt } from "jose";
import { z } from "zod";
import { auth, signIn, TELEGRAM_LOGIN_ENABLED } from "@/auth";
import { DEFAULT_LOCALE, LOCALES, type Locale } from "@/i18n/locales";
import { linkConsentOutcomeSchema, linkRequestView,
  type LinkConsentOutcome } from "@/lib/api/holder";
import { authenticationAssertion, identityBackend, managedSessionSchema,
  managementURL, type AuthenticationEvent } from "./management-session";
import { privateApiFetch } from "./private-api-fetch";

// Request-bound provider consent (`design/managed-identity-access/account-linking.md`,
// `management.md`): the holder's explicit approve/refuse/cancel mints a sealed,
// short-lived intent bound to the server-read request, the exact account, the
// private nonce and the operation identity. Only a matching fresh provider
// callback — the `account` object Auth.js hands the `jwt` callback — can turn
// that intent into a decision, signed as `sign_in` from the fresh event itself.
// A retained session is login proof, never consent: nothing here re-signs it.

const CONSENT_COOKIE = "nora-link-consent";
const RESULT_COOKIE = "nora-link-result";
const RESULT_PATH = "/api/v1/holder/link-consent";
const INTENT_SECONDS = 300;
const RESULT_SECONDS = 120;
const privateHeaders = { "Cache-Control": "private, no-store" };

const accountRef = z.object({ platform: z.enum(["discord", "telegram"]),
  kind: z.string().min(1), value: z.string().min(1) }).strict();
// The sealed payload also carries registered claims (iss/aud/iat/exp); only
// the intent fields are part of the shape — unknown keys are stripped.
// `locale` is the page locale the intent was started from, restricted to the
// routing allowlist so the return redirect cannot leave a known prefix.
const intentSchema = z.object({
  request_id: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/),
  side: z.enum(["initiating", "receiving"]),
  account: accountRef,
  action: z.enum(["approve", "refuse", "cancel"]),
  nonce: z.string().min(1),
  operation_id: z.string().min(1),
  flow_digest: z.string().regex(/^[a-f0-9]{64}$/),
  locale: z.enum(LOCALES).optional(),
});
type ConsentIntent = z.infer<typeof intentSchema>;

const actionFields = {
  request_id: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/),
  action: z.enum(["approve", "refuse", "cancel"]),
};
const actionSchema = z.object(actionFields).strict();
// A native browser form may carry the same two fields plus the page locale.
const formSchema = z.object({ ...actionFields,
  locale: z.enum(LOCALES).optional() }).strict();
type ConsentAction = { request_id: string; action: "approve" | "refuse" | "cancel";
  locale?: Locale };
const failure = z.object({ status: z.enum(["denied", "unavailable", "incomplete"]),
  detail: z.string().optional() }).strict();

// The outcome shape lives in src/lib/api/holder.ts — the request page's
// client side needs the type without importing this server-only module — and
// is re-exported here because auth.ts's import predates the move.
export { linkConsentOutcomeSchema } from "@/lib/api/holder";
export type { LinkConsentOutcome } from "@/lib/api/holder";

const reply = (status: string, code: number) =>
  Response.json({ status }, { status: code, headers: privateHeaders });

function intentKey(): Uint8Array {
  const secret = process.env.NORA_AUTHENTICATION_KEY ?? "";
  if (secret.length < 32) throw new Error("consent intent sealing is unavailable");
  return createHash("sha256")
    .update("nora.link-consent.v1\0").update(secret, "utf8").digest();
}

async function sealIntent(intent: ConsentIntent, requestExpiresAt: number): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  return new EncryptJWT({ ...intent })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM", typ: "nora.link-intent+jwt" })
    .setIssuer(process.env.NORA_AUTHENTICATION_ISSUER ?? "portal")
    .setAudience("nora-link-consent").setIssuedAt(now)
    .setExpirationTime(Math.min(now + INTENT_SECONDS, requestExpiresAt))
    .encrypt(intentKey());
}

async function unsealIntent(sealed: string): Promise<ConsentIntent | undefined> {
  try {
    const { payload, protectedHeader } = await jwtDecrypt(sealed, intentKey(), {
      issuer: process.env.NORA_AUTHENTICATION_ISSUER ?? "portal",
      audience: "nora-link-consent",
    });
    if (protectedHeader.typ !== "nora.link-intent+jwt") return undefined;
    const parsed = intentSchema.safeParse(payload);
    return parsed.success ? parsed.data : undefined;
  } catch {
    return undefined;
  }
}

async function readIntent(): Promise<ConsentIntent | undefined> {
  try {
    const sealed = (await cookies()).get(CONSENT_COOKIE)?.value;
    return sealed === undefined ? undefined : unsealIntent(sealed);
  } catch {
    return undefined;
  }
}

/** Decode the consent action from either client the endpoint serves: the JSON
 * client keeps its exact strict body; a same-origin native form posts
 * `application/x-www-form-urlencoded` and must carry ONLY the same fields
 * (plus the page locale) — a repeated or unexpected field refuses the body,
 * because a browser must not smuggle parameters the JSON client cannot send. */
async function readAction(request: Request): Promise<ConsentAction | undefined> {
  const type = request.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
  if (type === "application/json") {
    const parsed = actionSchema.safeParse(await request.json().catch(() => null));
    return parsed.success ? parsed.data : undefined;
  }
  if (type === "application/x-www-form-urlencoded") {
    let form: FormData;
    try {
      form = await request.formData();
    } catch {
      return undefined;
    }
    const fields: Record<string, string> = {};
    for (const [key, value] of form.entries()) {
      if (typeof value !== "string" || key in fields || !(key in formSchema.shape)) {
        return undefined;
      }
      fields[key] = value;
    }
    const parsed = formSchema.safeParse(fields);
    return parsed.success ? parsed.data : undefined;
  }
  return undefined;
}

/** The Auth.js consent hook: runs inside the `jwt` callback where `account` is
 * a genuine fresh provider authentication. It consumes the sealed intent only
 * when the provider proved the exact account the intent names, then submits
 * the decision signed `sign_in` from THAT fresh event — the only purpose a
 * provider callback may carry to the decision endpoint. */
export async function applyConsentIntent(
  fresh: AuthenticationEvent,
  callbackState?: string,
): Promise<LinkConsentOutcome | undefined> {
  const intent = await readIntent();
  if (!intent || !callbackState
    || intent.flow_digest !== createHash("sha256").update(callbackState).digest("hex")) {
    return undefined;
  }
  const base = { requestId: intent.request_id, action: intent.action,
    operationId: intent.operation_id, at: fresh.authenticatedAt };
  const expected = intent.account;
  const proven = fresh.account;
  if (expected.platform !== proven.platform || expected.kind !== proven.kind
    || expected.value !== proven.value) {
    return { ...base, outcome: "denied" };
  }
  const path = `/app/v1/holder/link-requests/${encodeURIComponent(intent.request_id)}/decisions`;
  const body = JSON.stringify({ operation_id: intent.operation_id,
    action: intent.action, nonce: intent.nonce });
  try {
    const assertion = await authenticationAssertion(fresh,
      { purpose: "sign_in", path, method: "POST", body });
    const response = await fetch(managementURL(path), {
      method: "POST", body, cache: "no-store", redirect: "error",
      headers: { Authorization: `Nora-Authentication ${assertion}`,
        "Content-Type": "application/json" },
      signal: AbortSignal.timeout(10_000),
    });
    const data: unknown = await response.json();
    if (response.status === 200) {
      const decided = z.object({ status: z.literal("ok"),
        state: z.string().min(1).optional() }).strict().safeParse(data);
      if (decided.success) {
        return { ...base, outcome: "complete",
          ...(decided.data.state ? { state: decided.data.state } : {}) };
      }
    } else {
      const refused = failure.safeParse(data);
      if (refused.success && (
        ([400, 401, 403].includes(response.status) && refused.data.status === "denied")
        || (response.status === 409 && refused.data.status === "incomplete")
        || (response.status === 503 && refused.data.status === "unavailable"))) {
        return { ...base, outcome: refused.data.status };
      }
    }
  } catch {
    // A failed submission never reports a commitment.
  }
  return { ...base, outcome: "unavailable" };
}

/** `POST /api/v1/holder/link-consent` — the explicit, same-origin consent
 * action. Reads the request through the retained-session boundary, binds it
 * into a sealed intent and starts a fresh provider authentication for the
 * account the REQUEST names, never one the client supplied. */
export async function consentAction(request: Request): Promise<Response> {
  try {
    const origin = new URL(process.env.AUTH_URL ?? request.url).origin;
    if (request.headers.get("Origin") !== origin) return reply("denied", 403);
    if (identityBackend() !== "managed") return reply("denied", 403);
    const session = await auth();
    const retained = managedSessionSchema.safeParse(session?.user?.managedIdentity);
    if (!retained.success) return reply("denied", 401);
    const parsed = await readAction(request);
    if (parsed === undefined) return reply("denied", 400);
    const { request_id, action } = parsed;
    const upstream = `/app/v1/holder/link-requests/${encodeURIComponent(request_id)}`;
    const result = await privateApiFetch(managementURL(upstream),
      { signal: AbortSignal.timeout(15_000) });
    const data: unknown = await result.json();
    const resolved = z.object({ status: z.literal("ok"), request: linkRequestView })
      .strict().safeParse(data);
    if (!(result.status === 200 && resolved.success)) {
      const refused = failure.safeParse(data);
      if (refused.success && [400, 401, 403].includes(result.status)
        && refused.data.status === "denied") {
        return reply("denied", result.status);
      }
      return reply("unavailable", 503);
    }
    const view = resolved.data.request;
    if (view.request_id !== request_id) return reply("unavailable", 503);
    if (view.state !== "pending" || Date.parse(view.expires_at) <= Date.now()
      || view.approved.includes(view.side)) {
      return reply("denied", 403);
    }
    const own = view.pair[view.side];
    const sessionAccount = retained.data.authentication.account;
    if (own.platform !== sessionAccount.platform || own.kind !== sessionAccount.kind
      || own.value !== sessionAccount.value) {
      return reply("unavailable", 503);
    }
    const provider = own.platform === "discord" || own.platform === "telegram"
      ? own.platform : undefined;
    if (provider === undefined
      || (provider === "telegram" && !TELEGRAM_LOGIN_ENABLED)) {
      return reply("denied", 403);
    }
    // `prompt=consent` shows Discord's authorization screen so the holder sees
    // which account is about to consent; Telegram's fresh callback itself is
    // the confirmation (its authorization endpoint accepts no such parameter).
    const url = await signIn(provider,
      { redirect: false, redirectTo: RESULT_PATH },
      provider === "discord" ? { prompt: "consent" } : undefined);
    if (typeof url !== "string" || !/^https?:\/\//.test(url)) {
      return reply("unavailable", 503);
    }
    const state = new URL(url).searchParams.get("state");
    if (!state) return reply("unavailable", 503);
    const sealed = await sealIntent({
      request_id, side: view.side, account: { ...own, platform: provider }, action,
      nonce: view.challenge.nonce, operation_id: randomUUID(),
      flow_digest: createHash("sha256").update(state).digest("hex"),
      ...(parsed.locale ? { locale: parsed.locale } : {}),
    }, Math.floor(Date.parse(view.expires_at) / 1000));
    const jar = await cookies();
    jar.set(CONSENT_COOKIE, sealed, {
      httpOnly: true, sameSite: "lax", path: "/", maxAge: INTENT_SECONDS,
      secure: origin.startsWith("https:"),
    });
    // Starting a new consent flow retires the outcome banner of any earlier
    // one, so a stale result cannot read as fresh on the request page.
    jar.delete(RESULT_COOKIE);
    return Response.redirect(url, 303);
  } catch {
    return reply("unavailable", 503);
  }
}

function resultKey(): Uint8Array {
  const secret = process.env.NORA_AUTHENTICATION_KEY ?? "";
  if (secret.length < 32) throw new Error("consent result sealing is unavailable");
  return createHash("sha256")
    .update("nora.link-result.v1\0").update(secret, "utf8").digest();
}

async function sealResult(outcome: LinkConsentOutcome,
  account: ConsentIntent["account"]): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  return new EncryptJWT({ ...outcome, account })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM", typ: "nora.link-result+jwt" })
    .setIssuer(process.env.NORA_AUTHENTICATION_ISSUER ?? "portal")
    .setAudience("nora-link-result").setIssuedAt(now)
    .setExpirationTime(now + RESULT_SECONDS)
    .encrypt(resultKey());
}

async function unsealResult(sealed: string): Promise<
  { outcome: LinkConsentOutcome; account: ConsentIntent["account"] } | undefined> {
  try {
    const { payload, protectedHeader } = await jwtDecrypt(sealed, resultKey(), {
      issuer: process.env.NORA_AUTHENTICATION_ISSUER ?? "portal",
      audience: "nora-link-result",
    });
    if (protectedHeader.typ !== "nora.link-result+jwt") return undefined;
    // Registered claims (iss/aud/iat/exp) ride the same payload; the strict
    // outcome shape is checked against the outcome fields alone.
    const { requestId, action, operationId, outcome, state, at } = payload;
    const parsed = linkConsentOutcomeSchema.safeParse(
      { requestId, action, operationId, outcome, state, at });
    const account = accountRef.safeParse(payload.account);
    return parsed.success && account.success
      ? { outcome: parsed.data, account: account.data } : undefined;
  } catch {
    return undefined;
  }
}

/** Consume the display outcome only under the exact account that decided.
 * This route owns cookie deletion; no outcome is serialized by a server page. */
export async function consumeLinkResult(request: Request): Promise<Response> {
  try {
    const jar = await cookies();
    const sealed = jar.get(RESULT_COOKIE)?.value;
    jar.delete(RESULT_COOKIE);
    if (identityBackend() !== "managed") return reply("denied", 403);
    const retained = managedSessionSchema.safeParse((await auth())?.user?.managedIdentity);
    if (!retained.success) return reply("denied", 401);
    const ids = new URL(request.url).searchParams.getAll("request_id");
    if (ids.length !== 1 || !/^[A-Za-z0-9_-]{1,128}$/.test(ids[0])) {
      return reply("denied", 400);
    }
    const result = sealed === undefined ? undefined : await unsealResult(sealed);
    if (result !== undefined) {
      const current = retained.data.authentication.account;
      if (result.account.platform !== current.platform || result.account.kind !== current.kind
        || result.account.value !== current.value) return reply("denied", 403);
    }
    return Response.json({ status: "ok", result:
      result?.outcome.requestId === ids[0] ? result.outcome : null },
    { headers: privateHeaders });
  } catch {
    return reply("unavailable", 503);
  }
}

/** `GET /api/v1/holder/link-consent` — the bounded return seam. Reports the
 * outcome of THIS intent only (matched by operation identity), then consumes
 * the cookie so nothing can be re-read or replayed from it.
 *
 * A top-level browser navigation (Accept: text/html, the provider's redirect
 * back) lands on a FIXED local page — the request page in the intent's own
 * locale, or /account when no intent is readable — and receives the actual
 * outcome through the sealed result cookie, never through the URL. Every other
 * client keeps the exact JSON result. */
export async function consentResult(request?: Request): Promise<Response> {
  try {
    const jar = await cookies();
    const sealed = jar.get(CONSENT_COOKIE)?.value;
    jar.delete(CONSENT_COOKIE);
    const intent = sealed === undefined ? undefined : await unsealIntent(sealed);
    if ((request?.headers.get("Accept") ?? "").includes("text/html")) {
      const origin = new URL(process.env.AUTH_URL ?? request!.url).origin;
      const accountPath = `/${intent?.locale ?? DEFAULT_LOCALE}/account`;
      if (identityBackend() !== "managed" || intent === undefined) {
        return Response.redirect(`${origin}${accountPath}`, 303);
      }
      const session = await auth();
      const outcome = linkConsentOutcomeSchema.safeParse(session?.user?.linkConsent);
      const retained = managedSessionSchema.safeParse(session?.user?.managedIdentity);
      const current = retained.success ? retained.data.authentication.account : undefined;
      if (outcome.success && intent.operation_id === outcome.data.operationId
        && outcome.data.requestId === intent.request_id
        && current?.platform === intent.account.platform && current.kind === intent.account.kind
        && current.value === intent.account.value) {
        jar.set(RESULT_COOKIE, await sealResult(outcome.data, intent.account), {
          httpOnly: true, sameSite: "lax", path: "/", maxAge: RESULT_SECONDS,
          secure: origin.startsWith("https:"),
        });
      }
      return Response.redirect(
        `${origin}${accountPath}/link-requests/${intent.request_id}`, 303);
    }
    if (identityBackend() !== "managed") return reply("denied", 403);
    const session = await auth();
    const outcome = linkConsentOutcomeSchema.safeParse(session?.user?.linkConsent);
    const retained = managedSessionSchema.safeParse(session?.user?.managedIdentity);
    const current = retained.success ? retained.data.authentication.account : undefined;
    if (!outcome.success || intent === undefined
      || intent.operation_id !== outcome.data.operationId
      || current?.platform !== intent.account.platform || current.kind !== intent.account.kind
      || current.value !== intent.account.value) {
      return reply("denied", 403);
    }
    const { outcome: status, requestId, action, state } = outcome.data;
    return Response.json(
      { status: status === "complete" ? "ok" : status, request_id: requestId,
        action, ...(state ? { state } : {}) },
      { status: { complete: 200, denied: 403, unavailable: 503,
        incomplete: 409 }[status], headers: privateHeaders });
  } catch {
    return reply("unavailable", 503);
  }
}
