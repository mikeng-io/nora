import { createHash, createHmac } from "node:crypto";
import { createServer, type Server } from "node:http";
import { jwtDecrypt } from "jose";
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
const { consentAction, consentResult } = await import("./link-consent");
const { GET, POST } = await import("@/app/api/v1/holder/link-consent/route");
const { GET: consumeResult } = await import("@/app/api/v1/holder/link-consent/result/route");

const key = "generated-holder-authentication-key-for-local-consent";
const issuer = "generated-portal";
const accountA = { platform: "discord", kind: "user_id", value: "generated-discord-a" };
const accountB = { platform: "signal", kind: "uuid", value: "generated-signal-b" };
const binding = { person_id: "generated-person", person_revision: "3",
  account_id: "generated-binding-a", account_revision: "5" };
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

let server: Server;
let calls: { path: string; body: string }[];

function claims(request: { url?: string; method?: string; headers: unknown },
  body: string) {
  const headers = request.headers as { authorization?: string };
  const token = (headers.authorization ?? "").replace(/^Nora-Authentication /, "");
  const [header, payload, signature] = token.split(".");
  if (!header || !payload
    || signature !== createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url")) {
    return null;
  }
  const c = JSON.parse(Buffer.from(payload, "base64url").toString());
  if (c.iss !== issuer || c.aud !== "nora-managed-api"
    || c.method !== request.method || c.path !== request.url
    || c.body_sha256 !== createHash("sha256").update(body).digest("hex")) {
    return null;
  }
  return c as Record<string, unknown>;
}

beforeEach(async () => {
  calls = [];
  jar.clear();
  startSignIn.mockClear();
  login.current = { user: { managedIdentity: identity } };
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
  vi.stubEnv("NORA_AUTHENTICATION_ISSUER", issuer);
  vi.stubEnv("AUTH_URL", "https://portal.generated.invalid");
  server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    const url = request.url ?? "";
    if (claims(request, body) === null) {
      response.writeHead(401, { "Content-Type": "application/json" });
      return response.end(JSON.stringify({ status: "denied" }));
    }
    calls.push({ path: url, body });
    if (request.method === "GET" && url.startsWith("/app/v1/holder/link-requests/")) {
      response.writeHead(200, { "Content-Type": "application/json" });
      return response.end(JSON.stringify({ status: "ok", request: link }));
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    return response.end(JSON.stringify({ status: "denied" }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No local listener");
  vi.stubEnv("NORA_API_BASE", `http://127.0.0.1:${address.port}`);
});
afterEach(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  vi.unstubAllEnvs();
});

function formRequest(fields: string, origin: string | null = "https://portal.generated.invalid",
  type = "application/x-www-form-urlencoded") {
  return new Request("https://portal.generated.invalid/api/v1/holder/link-consent", {
    method: "POST",
    headers: { "Content-Type": type, ...(origin ? { Origin: origin } : {}) },
    body: fields,
  });
}

function jsonRequest(body: unknown) {
  return new Request("https://portal.generated.invalid/api/v1/holder/link-consent", {
    method: "POST",
    headers: { "Content-Type": "application/json",
      Origin: "https://portal.generated.invalid" },
    body: JSON.stringify(body),
  });
}

function navigationRequest(url = "https://portal.generated.invalid/api/v1/holder/link-consent") {
  return new Request(url, {
    headers: { Accept: "text/html,application/xhtml+xml" },
  });
}

async function intentPayload() {
  const sealed = jar.get("nora-link-consent");
  if (sealed === undefined) return undefined;
  const intentKey = createHash("sha256")
    .update("nora.link-consent.v1\0").update(key, "utf8").digest();
  const { payload } = await jwtDecrypt(sealed, intentKey,
    { issuer, audience: "nora-link-consent" });
  return payload;
}

async function resultPayload() {
  const sealed = jar.get("nora-link-result");
  if (sealed === undefined) return undefined;
  const resultKey = createHash("sha256")
    .update("nora.link-result.v1\0").update(key, "utf8").digest();
  const { payload } = await jwtDecrypt(sealed, resultKey,
    { issuer, audience: "nora-link-result" });
  return payload;
}

describe("the browser form's consent action", () => {
  it("takes the same strict payload urlencoded and seals the page's locale", async () => {
    const response = await POST(formRequest(
      `request_id=${link.request_id}&action=approve&locale=en`));
    expect(response.status).toBe(303);
    expect(response.headers.get("Location"))
      .toBe("https://provider.invalid/authorize?state=generated-flow");
    expect(calls.map((c) => c.path))
      .toEqual([`/app/v1/holder/link-requests/${link.request_id}`]);
    expect(await intentPayload()).toMatchObject({
      request_id: link.request_id, action: "approve", locale: "en" });
  });

  it("refuses repeated, missing, extra or mistyped fields without touching anything", async () => {
    for (const fields of [
      // A repeated field: two request ids is not one.
      `request_id=${link.request_id}&request_id=another&action=approve`,
      `request_id=${link.request_id}&action=approve&action=refuse`,
      // A field the JSON client cannot send.
      `request_id=${link.request_id}&action=approve&actor=someone`,
      `request_id=${link.request_id}&action=approve&nonce=client-nonce`,
      `request_id=${link.request_id}&action=approve&return=https%3A%2F%2Fevil.invalid`,
      // Wrong shapes.
      `request_id=${link.request_id}&action=decide`,
      `request_id=../escape&action=approve`,
      `action=approve`,
      `request_id=${link.request_id}`,
      `request_id=${link.request_id}&action=approve&locale=fr`,
      ``,
    ]) {
      const response = await POST(formRequest(fields));
      expect(response.status, fields).toBe(400);
    }
    // A body that is neither JSON nor the form encoding is not the contract.
    expect((await POST(formRequest("request_id=x&action=approve",
      "https://portal.generated.invalid", "text/plain"))).status).toBe(400);
    expect((await POST(formRequest(`request_id=${link.request_id}&action=approve`,
      null))).status).toBe(403);
    expect((await POST(formRequest(`request_id=${link.request_id}&action=approve`,
      "https://foreign.invalid"))).status).toBe(403);
    expect(startSignIn).not.toHaveBeenCalled();
    expect(calls).toHaveLength(0);
    expect(jar.has("nora-link-consent")).toBe(false);
  });

  it("keeps the original JSON client working unchanged", async () => {
    const response = await POST(jsonRequest(
      { request_id: link.request_id, action: "refuse" }));
    expect(response.status).toBe(303);
    expect(await intentPayload()).toMatchObject({ action: "refuse" });
    expect((await intentPayload())?.locale).toBeUndefined();
  });

  it("retires a stale result banner when a new intent starts", async () => {
    jar.set("nora-link-result", "stale.result.value");
    expect((await POST(formRequest(
      `request_id=${link.request_id}&action=approve&locale=en`))).status).toBe(303);
    expect(jar.has("nora-link-result")).toBe(false);
  });
});

describe("the provider's navigation back", () => {
  const outcome = (operationId: unknown,
    over: Partial<Record<string, unknown>> = {}) => ({
    requestId: link.request_id, action: "approve", operationId,
    outcome: "complete", state: "pending", at: 1_790_000_000, ...over,
  });

  async function intend(locale = "en") {
    const response = await POST(formRequest(
      `request_id=${link.request_id}&action=approve&locale=${locale}`));
    expect(response.status).toBe(303);
    // The intent mints the operation id; the session outcome must match it.
    return (await intentPayload())?.operation_id;
  }

  it("lands on the request page in the intent's locale with the sealed result", async () => {
    const operationId = await intend();
    login.current = { user: { managedIdentity: identity,
      linkConsent: outcome(operationId) } };
    const response = await GET(navigationRequest());
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe(
      "https://portal.generated.invalid/en/account/link-requests/generated-request");
    expect(await resultPayload()).toMatchObject({
      requestId: link.request_id, outcome: "complete", state: "pending" });
    expect(jar.has("nora-link-consent")).toBe(false);
  });

  it("lands there for a refused outcome too — the page reads the result, not the URL", async () => {
    login.current = { user: { managedIdentity: identity,
      linkConsent: outcome(await intend("zh-Hant"),
        { outcome: "denied", state: undefined }) } };
    const response = await GET(navigationRequest());
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe(
      "https://portal.generated.invalid/zh-Hant/account/link-requests/generated-request");
    expect(await resultPayload()).toMatchObject({ outcome: "denied" });
  });

  it("lands without a banner when the session carries another or no outcome", async () => {
    await intend();
    login.current = { user: { managedIdentity: identity,
      linkConsent: outcome("another-operation") } };
    const first = await GET(navigationRequest());
    expect(first.headers.get("Location")).toBe(
      "https://portal.generated.invalid/en/account/link-requests/generated-request");
    expect(jar.has("nora-link-result")).toBe(false);
    // A bare navigation — no intent — never fabricates a page either.
    const second = await GET(navigationRequest());
    expect(second.headers.get("Location")).toBe(
      "https://portal.generated.invalid/zh-Hant/account");
    expect(jar.has("nora-link-result")).toBe(false);
  });

  it("never honours a return address the browser wrote", async () => {
    login.current = { user: { managedIdentity: identity,
      linkConsent: outcome(await intend()) } };
    const response = await GET(navigationRequest(
      "https://portal.generated.invalid/api/v1/holder/link-consent?return=https%3A%2F%2Fevil.invalid"));
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe(
      "https://portal.generated.invalid/en/account/link-requests/generated-request");
  });

  it("sends file mode to the account page, not a managed view", async () => {
    await intend();
    vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
    const response = await GET(navigationRequest());
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe(
      "https://portal.generated.invalid/en/account");
    expect(jar.has("nora-link-result")).toBe(false);
  });

  it("answers JSON clients exactly as before", async () => {
    login.current = { user: { managedIdentity: identity,
      linkConsent: outcome(await intend()) } };
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok",
      request_id: link.request_id, action: "approve", state: "pending" });
    expect(jar.has("nora-link-result")).toBe(false);
  });
});

describe("the account-bound single display result", () => {
  const display = (id = link.request_id) => consumeResult(new Request(
    `https://portal.generated.invalid/api/v1/holder/link-consent/result?request_id=${id}`));

  async function recorded() {
    expect((await POST(formRequest(
      `request_id=${link.request_id}&action=approve&locale=en`))).status).toBe(303);
    login.current = { user: { managedIdentity: identity,
      linkConsent: { requestId: link.request_id, action: "approve",
        operationId: (await intentPayload())?.operation_id, outcome: "complete",
        state: "committed", at: 1_790_000_000 } } };
    await GET(navigationRequest());
    expect(await resultPayload()).toMatchObject({ account: accountA, state: "committed" });
  }

  it("returns the deciding account's outcome once, never on a second read", async () => {
    await recorded();
    const first = await display();
    expect(first.headers.get("Cache-Control")).toContain("no-store");
    expect(await first.json()).toMatchObject({ status: "ok", result: {
      requestId: link.request_id, outcome: "complete", state: "committed" } });
    expect(jar.has("nora-link-result")).toBe(false);
    expect(await (await display()).json()).toEqual({ status: "ok", result: null });
  });

  it.each([
    ["another account of the same person", { ...accountA, value: "generated-discord-second" }],
    ["an unrelated account", { ...accountA, value: "generated-discord-unrelated" }],
    ["the receiving side", accountB],
  ])("refuses the prior outcome after switching to %s", async (_, account) => {
    await recorded();
    login.current = { user: { managedIdentity: {
      ...identity, authentication: { ...identity.authentication, account },
    } } };
    const refused = await display();
    expect(refused.status).toBe(403);
    expect(await refused.json()).toEqual({ status: "denied" });
    expect(jar.has("nora-link-result")).toBe(false);
  });

  it("returns no other request's, forged, or absent outcome", async () => {
    await recorded();
    expect(await (await display("another-request")).json()).toEqual({ status: "ok", result: null });
    jar.set("nora-link-result", "forged.result.value");
    expect(await (await display()).json()).toEqual({ status: "ok", result: null });
    expect(await (await display()).json()).toEqual({ status: "ok", result: null });
  });

  it("refuses a signed-out result read without serializing the outcome", async () => {
    await recorded();
    login.current = null;
    const response = await display();
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ status: "denied" });
    expect(jar.has("nora-link-result")).toBe(false);
  });
});
