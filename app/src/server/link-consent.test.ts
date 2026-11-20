import english from "../../messages/en.json";
import traditional from "../../messages/zh-Hant.json";
import { createHash, createHmac, randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const jar = vi.hoisted(() => new Map<string, string>());
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = jar.get(name);
      return value === undefined ? undefined : { name, value };
    },
    set: (name: string, value: string) => { jar.set(name, value); },
    delete: (name: string) => { jar.delete(name); },
  }),
  headers: async () => new Headers(),
}));
const login = vi.hoisted(() => ({ current: null as unknown }));
const startSignIn = vi.hoisted(() => vi.fn(async () => "https://provider.invalid/authorize?state=generated-flow"));
vi.mock("@/auth", () => ({
  auth: async () => login.current,
  signIn: startSignIn,
  TELEGRAM_LOGIN_ENABLED: true,
}));
const { consentAction, consentResult, applyConsentIntent } = await import("./link-consent");
const { GET, POST } = await import("@/app/api/v1/holder/link-consent/route");

const key = "generated-holder-authentication-key-for-local-consent";
const issuer = "generated-portal";
const accountA = { platform: "discord", kind: "user_id", value: "generated-discord-a" };
const accountB = { platform: "signal", kind: "uuid", value: "generated-signal-b" };
const binding = { person_id: "generated-person", person_revision: "3",
  account_id: "generated-binding-a", account_revision: "5" };
// A retained sign-in from a day ago: current for `session` purpose, never
// re-mintable as `sign_in` by this unit.
const authenticatedAt = Math.floor(Date.now() / 1000) - 86_400;
const identity = {
  authentication: { sessionId: "generated-session", account: accountA,
    method: "discord_oauth", proofIssuer: "generated-discord-client",
    authenticatedAt },
  binding,
};
const link = {
  request_id: "generated-request", side: "initiating", state: "pending",
  pair: { initiating: accountA, receiving: accountB },
  surviving_person: "generated-person", created_at: "2026-11-01T00:00:00+00:00",
  expires_at: "2099-11-01T00:15:00+00:00", approved: [],
  challenge: { nonce: "generated-private-nonce", approve: "generated-approve",
    refuse: "generated-refuse" },
};
const freshEvent = (over: Partial<Record<string, unknown>> = {}) => ({
  sessionId: randomUUID(),
  account: { platform: "discord" as const, kind: "user_id", value: "generated-discord-a" },
  method: "discord_oauth" as const, proofIssuer: "generated-discord-client",
  authenticatedAt: Math.floor(Date.now() / 1000), ...over,
});

let server: Server;
let calls: { path: string; body: string; claims: Record<string, unknown> }[];
let decisionVerdict: { code: number; body: unknown };
let loseDecisionResponse: boolean;
let viewCode: number;
let viewBody: unknown;

function claims(request: { url?: string; method?: string; headers: unknown },
  body: string) {
  const headers = request.headers as { authorization?: string };
  const token = (headers.authorization ?? "").replace(/^Nora-Authentication /, "");
  const [header, payload, signature] = token.split(".");
  if (!header || !payload
    || signature !== createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url")
    || JSON.parse(Buffer.from(header, "base64url").toString()).typ !== "nora.authn+jwt") {
    return null;
  }
  const c = JSON.parse(Buffer.from(payload, "base64url").toString());
  const provenance: Record<string, string> = { discord_oauth: "generated-discord-client",
    telegram_oidc: "generated-telegram-client" };
  const platforms: Record<string, string> = { discord_oauth: "discord",
    telegram_oidc: "telegram", signal_code: "signal", whatsapp_code: "whatsapp" };
  if (c.iss !== issuer || c.aud !== "nora-managed-api"
    || !(0 < c.exp - c.iat && c.exp - c.iat <= 60)
    || c.method !== request.method || c.path !== request.url
    || c.body_sha256 !== createHash("sha256").update(body).digest("hex")
    || provenance[c.authentication_method] !== c.proof_issuer
    || platforms[c.authentication_method] !== c.account?.platform) {
    return null;
  }
  // The verifier's freshness rule: sign_in must be minted within a minute of
  // the authentication it asserts; a retained session may be older.
  const age = c.iat - c.authenticated_at;
  if (age < 0 || age > (c.purpose === "sign_in" ? 60 : 30 * 86400)) return null;
  return c as Record<string, unknown>;
}

function deny(response: { writeHead: Function; end: Function }, code = 401) {
  response.writeHead(code, { "Content-Type": "application/json" });
  response.end(JSON.stringify({ status: "denied" }));
}

beforeEach(async () => {
  calls = [];
  loseDecisionResponse = false;
  jar.clear();
  startSignIn.mockClear();
  login.current = { user: { managedIdentity: identity } };
  decisionVerdict = { code: 200, body: { status: "ok", state: "pending" } };
  viewCode = 200;
  viewBody = { status: "ok", request: link };
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
  vi.stubEnv("NORA_AUTHENTICATION_ISSUER", issuer);
  vi.stubEnv("AUTH_URL", "https://portal.generated.invalid");
  server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    const c = claims(request, body);
    const url = request.url ?? "";
    if (c === null) return deny(response);
    calls.push({ path: url, body, claims: c });
    if (request.method === "GET" && url.startsWith("/app/v1/holder/link-requests/")) {
      // The session boundary: the retained binding must be attached verbatim.
      if (c.purpose !== "session"
        || JSON.stringify(c.binding) !== JSON.stringify(identity.binding)) {
        return deny(response);
      }
      response.writeHead(viewCode, { "Content-Type": "application/json" });
      return response.end(JSON.stringify(viewBody));
    }
    if (request.method === "POST" && url.endsWith("/decisions")) {
      // The decision boundary mirrors `HolderLinkService.decide`: sign_in
      // only, never a retained binding, the exact account a request names.
      if (c.purpose !== "sign_in" || c.binding !== undefined) return deny(response);
      const decided = JSON.parse(body);
      const side = ["initiating", "receiving"].find((s) =>
        JSON.stringify(link.pair[s as "initiating" | "receiving"])
        === JSON.stringify(c.account));
      if (side === undefined || decided.nonce !== link.challenge.nonce) {
        return deny(response, 403);
      }
      if (loseDecisionResponse) {
        viewBody = { status: "ok", request: { ...link, state: "committed" } };
        response.destroy(); return;
      }
      response.writeHead(decisionVerdict.code, { "Content-Type": "application/json" });
      return response.end(JSON.stringify(decisionVerdict.body));
    }
    return deny(response, 404);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No local listener");
  vi.stubEnv("NORA_API_BASE", `http://127.0.0.1:${address.port}`);
});
afterEach(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

function actionRequest(body: unknown, origin: string | null = "https://portal.generated.invalid") {
  return new Request("https://portal.generated.invalid/api/v1/holder/link-consent", {
    method: "POST",
    headers: { "Content-Type": "application/json",
      ...(origin ? { Origin: origin } : {}) },
    body: JSON.stringify(body),
  });
}

describe("the consent action", () => {
  it("binds the server-read request into a fresh provider redirect", async () => {
    const response = await POST(actionRequest({ request_id: link.request_id, action: "approve" }));
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("https://provider.invalid/authorize?state=generated-flow");
    expect(startSignIn).toHaveBeenCalledWith("discord",
      { redirect: false, redirectTo: "/api/v1/holder/link-consent" },
      { prompt: "consent" });
    expect(calls.map((c) => c.path)).toEqual(
      [`/app/v1/holder/link-requests/${link.request_id}`]);
    // The intent rides the callback only as a sealed, expiring cookie —
    // the response carries no plaintext account, nonce or actor.
    expect(jar.get("nora-link-consent")).toEqual(expect.any(String));
    expect(await response.text()).toBe("");
  });

  it("refuses foreign or missing origins, file mode, missing session and smuggled fields", async () => {
    for (const origin of [null, "https://foreign.invalid"]) {
      expect((await POST(actionRequest({ request_id: "generated-request", action: "approve" }, origin))).status).toBe(403);
    }
    vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
    expect((await POST(actionRequest({ request_id: "generated-request", action: "approve" }))).status).toBe(403);
    vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
    login.current = null;
    expect((await POST(actionRequest({ request_id: "generated-request", action: "approve" }))).status).toBe(401);
    login.current = { user: { managedIdentity: identity } };
    for (const body of [{}, { action: "approve" },
      { request_id: "generated-request", action: "approve", actor: "someone" },
      { request_id: "generated-request", action: "approve", nonce: "client-nonce" },
      { request_id: "generated-request", action: "approve", proof: {} },
      { request_id: "../escape", action: "approve" },
      { request_id: "generated-request", action: "decide" }]) {
      expect((await POST(actionRequest(body))).status).toBe(400);
    }
    expect(startSignIn).not.toHaveBeenCalled();
    expect(calls).toHaveLength(0);
    expect(jar.has("nora-link-consent")).toBe(false);
  });

  it("stays refused or unavailable on upstream refusals and never invents a request", async () => {
    viewCode = 403;
    viewBody = { status: "denied" };
    expect((await POST(actionRequest({ request_id: "generated-request", action: "approve" }))).status).toBe(403);
    viewCode = 503;
    viewBody = { status: "unavailable" };
    expect((await POST(actionRequest({ request_id: "generated-request", action: "approve" }))).status).toBe(503);
    for (const malformed of [{ status: "ok" }, { status: "ok", request: {} },
      { status: "ok", request: { ...link, request_id: "another" } }, "text"]) {
      viewCode = 200;
      viewBody = malformed;
      expect((await POST(actionRequest({ request_id: "generated-request", action: "approve" }))).status).toBe(503);
    }
    expect(startSignIn).not.toHaveBeenCalled();
    expect(jar.has("nora-link-consent")).toBe(false);
  });

  it("refuses requests that are not pending, expired, already answered or owned by another account", async () => {
    for (const variant of [
      { ...link, state: "committed" }, { ...link, state: "refused" },
      { ...link, expires_at: "2001-11-01T00:15:00+00:00" },
      { ...link, approved: ["initiating"] },
      { ...link, pair: { ...link.pair, initiating: { ...accountA, value: "another-account" } } },
      { ...link, pair: { ...link.pair, initiating: accountB }, side: "receiving" },
    ]) {
      viewBody = { status: "ok", request: variant };
      const response = await POST(actionRequest({ request_id: link.request_id, action: "approve" }));
      expect([403, 503]).toContain(response.status);
      expect(jar.has("nora-link-consent")).toBe(false);
    }
    expect(startSignIn).not.toHaveBeenCalled();
  });
});

describe("the fresh callback", () => {
  async function intend() {
    expect((await POST(actionRequest({ request_id: link.request_id, action: "approve" }))).status).toBe(303);
    calls.length = 0;
  }

  it("lets only the matching fresh provider event submit the bound decision", async () => {
    await intend();
    const fresh = freshEvent();
    const outcome = await applyConsentIntent(fresh, "generated-flow");
    expect(outcome).toMatchObject({ requestId: link.request_id, action: "approve",
      outcome: "complete", state: "pending", at: fresh.authenticatedAt });
    expect(calls).toHaveLength(1);
    const decision = calls[0];
    expect(decision.path).toBe(`/app/v1/holder/link-requests/${link.request_id}/decisions`);
    expect(JSON.parse(decision.body)).toEqual({ operation_id: expect.any(String),
      action: "approve", nonce: "generated-private-nonce" });
    expect(decision.claims.purpose).toBe("sign_in");
    expect(decision.claims.binding).toBeUndefined();
    expect(decision.claims.account).toEqual(accountA);
    expect(decision.claims.authentication_method).toBe("discord_oauth");
    expect(decision.claims.authenticated_at).toBe(fresh.authenticatedAt);
  });

  it("never submits for a different account, no intent, a tampered or an expired intent", async () => {
    // No intent at all: a bare sign-in does nothing.
    expect(await applyConsentIntent(freshEvent(), "generated-flow")).toBeUndefined();
    await intend();
    const wrong = freshEvent({ account: { platform: "discord", kind: "user_id", value: "another-account" } });
    expect(await applyConsentIntent(wrong, "generated-flow")).toMatchObject({ outcome: "denied" });
    expect(calls).toHaveLength(0);
    jar.set("nora-link-consent", "forged.intent.value");
    expect(await applyConsentIntent(freshEvent(), "generated-flow")).toBeUndefined();
    jar.clear();
    await intend();
    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + 400_000);
    expect(await applyConsentIntent(freshEvent(), "generated-flow")).toBeUndefined();
    vi.useRealTimers();
    expect(calls).toHaveLength(0);
  });

  it("requires the exact authorization flow even for the matching account", async () => {
    await intend();
    expect(await applyConsentIntent(freshEvent())).toBeUndefined();
    expect(await applyConsentIntent(freshEvent(), "another-flow")).toBeUndefined();
    expect(calls).toHaveLength(0);
    expect(await applyConsentIntent(freshEvent(), "generated-flow"))
      .toMatchObject({ outcome: "complete" });
    expect(calls).toHaveLength(1);
  });

  it("keeps denial, unavailability and partial commitment distinct and never reports success", async () => {
    for (const [code, status, outcome] of [[403, "denied", "denied"],
      [503, "unavailable", "unavailable"], [409, "incomplete", "incomplete"]] as const) {
      await intend();
      decisionVerdict = { code, body: { status } };
      expect(await applyConsentIntent(freshEvent(), "generated-flow")).toMatchObject({ outcome });
    }
    await intend();
    decisionVerdict = { code: 200, body: { status: "ok", extra: "contradiction" } };
    expect(await applyConsentIntent(freshEvent(), "generated-flow")).toMatchObject({ outcome: "unavailable" });
    // The last submission is the only call since `intend` clears the log.
    expect(calls).toHaveLength(1);
  });
});

describe("the consent result", () => {
  it("reports this intent's outcome once, then forgets it", async () => {
    const response = await POST(actionRequest({ request_id: link.request_id, action: "refuse" }));
    expect(response.status).toBe(303);
    const fresh = freshEvent();
    await applyConsentIntent(fresh, "generated-flow");
    const decision = calls.find((c) => c.path.endsWith("/decisions"));
    const opId = decision && JSON.parse(decision.body).operation_id;
    login.current = { user: { managedIdentity: identity,
      linkConsent: { requestId: link.request_id, action: "refuse", operationId: opId,
        outcome: "complete", state: "refused", at: fresh.authenticatedAt } } };
    const result = await GET();
    expect(result.status).toBe(200);
    expect(await result.json()).toEqual({ status: "ok", request_id: link.request_id,
      action: "refuse", state: "refused" });
    expect(jar.has("nora-link-consent")).toBe(false);
    expect((await GET()).status).toBe(403);
  });

  it("cannot attribute a different intent's outcome or report without a session result", async () => {
    await POST(actionRequest({ request_id: link.request_id, action: "approve" }));
    login.current = { user: { managedIdentity: identity,
      linkConsent: { requestId: link.request_id, action: "approve",
        operationId: "another-operation", outcome: "complete", at: 1_790_000_000 } } };
    expect((await GET()).status).toBe(403);
    login.current = { user: { managedIdentity: identity } };
    await POST(actionRequest({ request_id: link.request_id, action: "approve" }));
    expect((await GET()).status).toBe(403);
    expect(jar.has("nora-link-consent")).toBe(false);
  });
});

it("keeps a post-commit lost response unknown rather than claiming no change", async () => {
  const response = await consentAction(actionRequest({ request_id: link.request_id, action: "approve" }));
  expect(response.status).toBe(303);
  loseDecisionResponse = true;
  const result = await applyConsentIntent(freshEvent(), "generated-flow");
  expect(calls.filter((call) => call.path.endsWith("/decisions"))).toHaveLength(1);
  expect(result?.outcome).toBe("unavailable");
  expect(result?.state).toBeUndefined();
  expect(viewBody).toMatchObject({ request: { state: "committed" } });
  expect(english.accountPage.result.unavailable).toContain("couldn't confirm");
  expect(english.accountPage.result.unavailable).not.toContain("nothing changed");
  expect(traditional.accountPage.result.unavailable).toContain("確認唔到");
  expect(traditional.accountPage.result.unavailable).not.toContain("咩都冇變");
});
