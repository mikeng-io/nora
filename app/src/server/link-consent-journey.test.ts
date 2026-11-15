import { createHash, createHmac } from "node:crypto";
import type { Server } from "node:http";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// The zero-cost provider rehearsal: a generated OAuth 2.0 server answers for
// Discord and a generated OIDC server (discovery, RS256 id tokens, nonce) for
// Telegram. The real `next-auth` package, its real provider modules and the
// real `jwt`/`session` callbacks run unmocked — only the destinations and the
// request cookies are generated here.
vi.mock("server-only", () => ({}));
vi.mock("next/navigation", () => ({
  redirect: (url: string) => { throw new Error(`unexpected redirect to ${url}`); },
}));
// `next-auth`'s env shim imports `next/server` by bare specifier; under Node
// ESM it never resolves, and `reqWithEnvURL` only constructs a NextRequest
// when AUTH_URL rewrites the request — this test never sets it.
vi.mock("next/server", () => ({
  NextRequest: class NextRequest extends Request {
    get nextUrl() { return new URL(this.url); }
  },
}));
const jar = vi.hoisted(() => new Map<string, string>());
const current = vi.hoisted(() => ({ headers: new Headers() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = jar.get(name);
      return value === undefined ? undefined : { name, value };
    },
    set: (name: string, value: string) => { jar.set(name, value); },
    delete: (name: string) => { jar.delete(name); },
  }),
  headers: async () => current.headers,
}));

const key = "generated-holder-authentication-key-for-local-consent";
const issuer = "generated-portal";
const shared = vi.hoisted(() => {
  type Call = { path: string; body: string; claims: Record<string, unknown> };
  const control = {
    // The account the generated providers currently authenticate as.
    discordId: "generated-discord-a",
    telegramId: 427311,
    // The private request the generated backend currently resolves.
    link: null as Record<string, unknown> | null,
    calls: [] as Call[],
    prompt: null as string | null,
    servers: [] as import("node:http").Server[],
  };
  let pending: Promise<{ backend: string; discord: string; oidc: string }> | null = null;
  const start = async () => {
    const http = await import("node:http");
    const jose = await import("jose");
    const { privateKey, publicKey } = await jose.generateKeyPair("RS256");
    const jwk = await jose.exportJWK(publicKey);
    jwk.kid = "generated-oidc-key";
    jwk.alg = "RS256";
    jwk.use = "sig";
    const nonce = { current: "" };
    const codes = new Map<string, { redirect_uri: string; client: string }>();
    const accounts: Record<string, { binding: Record<string, string>; display_name: string }> = {
      "discord:generated-discord-a": { binding: { person_id: "generated-person",
        person_revision: "3", account_id: "generated-binding-a", account_revision: "5" },
        display_name: "Generated A" },
      "discord:generated-discord-b": { binding: { person_id: "generated-person",
        person_revision: "3", account_id: "generated-binding-b", account_revision: "1" },
        display_name: "Generated B" },
      "telegram:427311": { binding: { person_id: "generated-person",
        person_revision: "3", account_id: "generated-binding-t", account_revision: "2" },
        display_name: "Generated T" },
    };
    const provenance: Record<string, string> = { discord_oauth: "generated-discord-client",
      telegram_oidc: "generated-telegram-client" };
    const platforms: Record<string, string> = { discord_oauth: "discord",
      telegram_oidc: "telegram", signal_code: "signal", whatsapp_code: "whatsapp" };
    const claims = (request: import("node:http").IncomingMessage, body: string) => {
      const token = (request.headers.authorization ?? "")
        .replace(/^Nora-Authentication /, "");
      const [header, payload, signature] = token.split(".");
      if (!header || !payload
        || signature !== createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url")) {
        return null;
      }
      const head = JSON.parse(Buffer.from(header, "base64url").toString());
      if (set(head) !== "alg,kid,typ" || head.alg !== "HS256" || head.typ !== "nora.authn+jwt"
        || head.kid !== issuer) {
        return null;
      }
      const c = JSON.parse(Buffer.from(payload, "base64url").toString());
      const age = c.iat - c.authenticated_at;
      if (c.iss !== issuer || c.aud !== "nora-managed-api"
        || !(0 < c.exp - c.iat && c.exp - c.iat <= 60)
        || c.method !== request.method || c.path !== request.url
        || c.body_sha256 !== createHash("sha256").update(body).digest("hex")
        || age < 0 || age > (c.purpose === "sign_in" ? 60 : 30 * 86400)
        || provenance[c.authentication_method] !== c.proof_issuer
        || platforms[c.authentication_method] !== c.account?.platform) {
        return null;
      }
      if ((c.purpose === "session" || ["signal_code", "whatsapp_code"]
        .includes(c.authentication_method)) === (c.binding === undefined)) {
        return null;
      }
      return c as Record<string, unknown>;
    };
    const set = (o: object) => Object.keys(o).sort().join(",");
    const json = (response: import("node:http").ServerResponse, code: number, data: unknown) => {
      response.writeHead(code, { "Content-Type": "application/json" });
      response.end(JSON.stringify(data));
    };
    const denied = (response: import("node:http").ServerResponse, code = 401) =>
      json(response, code, { status: "denied" });
    const read = async (request: import("node:http").IncomingMessage) => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      return Buffer.concat(chunks).toString();
    };
    const form = (body: string) => Object.fromEntries(new URLSearchParams(body));
    const backend = http.createServer(async (request, response) => {
      const body = await read(request);
      const c = claims(request, body);
      const url = request.url ?? "";
      if (c === null) return denied(response);
      control.calls.push({ path: url, body, claims: c });
      const proven = c.account as { platform: string; value: string };
      const accountKey = `${proven.platform}:${proven.value}`;
      if (request.method === "POST" && url === "/app/v1/management/auth/exchange") {
        if (c.purpose !== "sign_in" || body !== "{}") return denied(response);
        const known = accounts[accountKey];
        if (!known) return denied(response, 403);
        return json(response, 200, { status: "ok", binding: known.binding,
          display_name: known.display_name });
      }
      if (request.method === "GET" && url.startsWith("/app/v1/holder/link-requests/")) {
        if (c.purpose !== "session"
          || JSON.stringify(c.binding) !== JSON.stringify(accounts[accountKey]?.binding)) {
          return denied(response);
        }
        const link = control.link;
        if (!link || link.request_id !== url.split("/").pop()) return denied(response, 404);
        return json(response, 200, { status: "ok", request: link });
      }
      if (request.method === "POST" && url.endsWith("/decisions")) {
        if (c.purpose !== "sign_in" || c.binding !== undefined) return denied(response);
        const link = control.link;
        if (!link) return denied(response, 404);
        const decided = JSON.parse(body);
        const side = ["initiating", "receiving"].find((s) =>
          JSON.stringify((link.pair as Record<string, unknown>)[s]) === JSON.stringify(c.account));
        if (side === undefined || decided.nonce !== (link.challenge as { nonce: string }).nonce
          || !["approve", "refuse", "cancel"].includes(decided.action)
          || typeof decided.operation_id !== "string") {
          return denied(response, 403);
        }
        return json(response, 200, { status: "ok", state: "pending" });
      }
      return denied(response, 404);
    });
    const discord = http.createServer(async (request, response) => {
      const url = new URL(request.url ?? "/", "http://localhost");
      if (request.method === "GET" && url.pathname === "/authorize") {
        const p = url.searchParams;
        const redirect_uri = p.get("redirect_uri") ?? "";
        if (p.get("client_id") !== "generated-discord-client"
          || redirect_uri !== "http://localhost/api/auth/callback/discord"
          || p.get("response_type") !== "code" || !p.get("code_challenge")) {
          return json(response, 400, { error: "invalid_request" });
        }
        control.prompt = p.get("prompt");
        const code = `generated-discord-code-${codes.size}`;
        codes.set(code, { redirect_uri, client: "discord" });
        // Discord's default `checks` is pkce-only: no state to echo unless sent.
        const state = p.get("state");
        const target = `${redirect_uri}?code=${code}`
          + (state === null ? "" : `&state=${encodeURIComponent(state)}`)
          + `&iss=${encodeURIComponent(discordBase)}`;
        response.writeHead(302, { Location: target });
        return response.end();
      }
      if (request.method === "POST" && url.pathname === "/token") {
        const p = form(await read(request));
        const grant = codes.get(p.code ?? "");
        const [clientId, clientSecret] = Buffer.from(
          (request.headers.authorization ?? "").replace(/^Basic /, ""), "base64")
          .toString().split(":");
        if (p.grant_type !== "authorization_code" || !grant || grant.client !== "discord"
          || p.redirect_uri !== grant.redirect_uri || !p.code_verifier
          || clientId !== "generated-discord-client"
          || clientSecret !== "generated-discord-secret") {
          return json(response, 400, { error: "invalid_grant" });
        }
        codes.delete(p.code ?? "");
        return json(response, 200, { access_token: "generated-discord-access",
          token_type: "Bearer", expires_in: 3600, scope: "identify" });
      }
      if (request.method === "GET" && url.pathname === "/api/users/@me") {
        if (request.headers.authorization !== "Bearer generated-discord-access") {
          return json(response, 401, { message: "401: Unauthorized" });
        }
        return json(response, 200, { id: control.discordId,
          username: "generated-user", avatar: "generated-avatar", discriminator: "0",
          global_name: "Generated User" });
      }
      return json(response, 404, { error: "not_found" });
    });
    const oidc = http.createServer(async (request, response) => {
      const url = new URL(request.url ?? "/", "http://localhost");
      if (request.method === "GET" && url.pathname === "/.well-known/openid-configuration") {
        return json(response, 200, {
          issuer: oidcBase, authorization_endpoint: `${oidcBase}/auth`,
          token_endpoint: `${oidcBase}/token`, jwks_uri: `${oidcBase}/jwks.json`,
          userinfo_endpoint: `${oidcBase}/userinfo`,
          response_types_supported: ["code"], subject_types_supported: ["public"],
          grant_types_supported: ["authorization_code"],
          id_token_signing_alg_values_supported: ["RS256"],
          scopes_supported: ["openid", "profile"],
          token_endpoint_auth_methods_supported: ["client_secret_basic"],
        });
      }
      if (request.method === "GET" && url.pathname === "/auth") {
        const p = url.searchParams;
        const redirect_uri = p.get("redirect_uri") ?? "";
        if (p.get("client_id") !== "generated-telegram-client"
          || redirect_uri !== "http://localhost/api/auth/callback/telegram"
          || p.get("response_type") !== "code" || !p.get("state")
          || !p.get("nonce") || !p.get("code_challenge")) {
          return json(response, 400, { error: "invalid_request" });
        }
        nonce.current = p.get("nonce") ?? "";
        const code = `generated-oidc-code-${codes.size}`;
        codes.set(code, { redirect_uri, client: "telegram" });
        response.writeHead(302, { Location: `${redirect_uri}?code=${code}`
          + `&state=${encodeURIComponent(p.get("state") ?? "")}`
          + `&iss=${encodeURIComponent(oidcBase)}` });
        return response.end();
      }
      if (request.method === "POST" && url.pathname === "/token") {
        const p = form(await read(request));
        const grant = codes.get(p.code ?? "");
        const [clientId, clientSecret] = Buffer.from(
          (request.headers.authorization ?? "").replace(/^Basic /, ""), "base64")
          .toString().split(":");
        if (p.grant_type !== "authorization_code" || !grant || grant.client !== "telegram"
          || p.redirect_uri !== grant.redirect_uri || !p.code_verifier
          || clientId !== "generated-telegram-client"
          || clientSecret !== "generated-telegram-secret") {
          return json(response, 400, { error: "invalid_grant" });
        }
        codes.delete(p.code ?? "");
        const now = Math.floor(Date.now() / 1000);
        const idToken = await new jose.SignJWT({
          id: control.telegramId, first_name: "Generated", username: "generated_telegram",
          nonce: nonce.current,
        }).setProtectedHeader({ alg: "RS256", kid: "generated-oidc-key", typ: "JWT" })
          .setIssuer(oidcBase).setSubject("generated-subject-42")
          .setAudience("generated-telegram-client").setIssuedAt(now)
          .setExpirationTime(now + 600).sign(privateKey);
        return json(response, 200, { access_token: "generated-oidc-access",
          token_type: "Bearer", expires_in: 3600, id_token: idToken });
      }
      if (request.method === "GET" && url.pathname === "/jwks.json") {
        return json(response, 200, { keys: [jwk] });
      }
      if (request.method === "GET" && url.pathname === "/userinfo") {
        return json(response, 200, { sub: "generated-subject-42" });
      }
      return json(response, 404, { error: "not_found" });
    });
    const listen = (server: Server) => new Promise<string>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => {
        const address = server.address();
        if (!address || typeof address === "string") reject(new Error("no listener"));
        else resolve(`http://127.0.0.1:${address.port}`);
      });
    });
    const [backendBase, discordBase, oidcBase] = await Promise.all(
      [backend, discord, oidc].map(listen));
    control.servers = [backend, discord, oidc];
    return { backend: backendBase, discord: discordBase, oidc: oidcBase };
  };
  return { control, start: () => (pending ??= start()) };
});

vi.mock("../discord", async () => {
  const { discord } = await shared.start();
  const { default: Discord } = await import("next-auth/providers/discord");
  return {
    discordProvider: (credentials: { clientId?: string; clientSecret?: string }) => Discord({
      issuer: discord, clientId: credentials.clientId, clientSecret: credentials.clientSecret,
      authorization: { url: `${discord}/authorize`, params: { scope: "identify" } },
      token: { url: `${discord}/token` },
      userinfo: { url: `${discord}/api/users/@me` },
    }),
  };
});
vi.mock("../telegram", async () => {
  const { oidc } = await shared.start();
  const { createRemoteJWKSet, jwtVerify } = await import("jose");
  const jwks = createRemoteJWKSet(new URL(`${oidc}/jwks.json`));
  return {
    createTelegramProvider: (credentials: { clientId?: string; clientSecret?: string }) => ({
      id: "telegram", name: "Telegram", type: "oidc", issuer: oidc,
      clientId: credentials.clientId, clientSecret: credentials.clientSecret,
      authorization: { url: `${oidc}/auth`, params: { scope: "openid profile" } },
      token: { conform: async (response: Response) => {
        const data: Record<string, unknown> = await response.clone().json();
        if (typeof data.id_token !== "string") return response;
        try {
          await jwtVerify(data.id_token, jwks,
            { requiredClaims: ["iss", "sub", "aud", "exp", "iat", "nonce", "id"] });
          return response;
        } catch {
          return Response.json({ error: "invalid_token" }, { status: 401 });
        }
      } },
      client: { token_endpoint_auth_method: "client_secret_basic",
        id_token_signed_response_alg: "RS256" },
      checks: ["pkce", "state", "nonce"], idToken: true,
      // The Bot API numeric `id` claim is the account key, never `sub`.
      profile: (profile: { id?: number | bigint; first_name?: string; username?: string }) => ({
        id: profile.id === undefined ? "" : String(profile.id),
        name: profile.first_name ?? profile.username ?? "generated",
        image: undefined,
      }),
    }),
  };
});

vi.stubEnv("AUTH_SECRET", "generated-auth-secret-for-local-journeys-32-bytes");
vi.stubEnv("AUTH_DISCORD_ID", "generated-discord-client");
vi.stubEnv("AUTH_DISCORD_SECRET", "generated-discord-secret");
vi.stubEnv("AUTH_TELEGRAM_ID", "generated-telegram-client");
vi.stubEnv("AUTH_TELEGRAM_SECRET", "generated-telegram-secret");
vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
vi.stubEnv("NORA_AUTHENTICATION_ISSUER", issuer);

const { handlers, signIn } = await import("@/auth");
const { GET, POST } = await import("@/app/api/v1/holder/link-consent/route");

const discordLink = {
  request_id: "generated-request", side: "initiating", state: "pending",
  pair: { initiating: { platform: "discord", kind: "user_id", value: "generated-discord-a" },
    receiving: { platform: "signal", kind: "uuid", value: "generated-signal-b" } },
  surviving_person: "generated-person", created_at: "2026-11-01T00:00:00+00:00",
  expires_at: "2099-11-01T00:15:00+00:00", approved: [],
  challenge: { nonce: "generated-private-nonce", approve: "generated-approve",
    refuse: "generated-refuse" },
};
const telegramLink = { ...discordLink, request_id: "generated-request-t",
  pair: { initiating: { platform: "telegram", kind: "user_id", value: "427311" },
    receiving: discordLink.pair.receiving } };

beforeAll(async () => {
  const { backend } = await shared.start();
  vi.stubEnv("NORA_API_BASE", backend);
});
beforeEach(() => {
  jar.clear();
  shared.control.calls = [];
  shared.control.prompt = null;
  shared.control.discordId = "generated-discord-a";
  shared.control.telegramId = 427311;
  shared.control.link = discordLink;
});
afterAll(() => {
  for (const server of shared.control.servers) server.close();
  vi.unstubAllEnvs();
});

const cookieHeader = () => [...jar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
function appHeaders(extra: Record<string, string> = {}) {
  return new Headers({ host: "localhost", "x-forwarded-proto": "http",
    cookie: cookieHeader(), ...extra });
}
const applyCookies = (response: Response) => {
  for (const line of response.headers.getSetCookie()) {
    const [pair] = line.split(";");
    const cut = pair.indexOf("=");
    const name = pair.slice(0, cut).trim();
    const value = pair.slice(cut + 1).trim();
    if (/expires=[^;]*1970/i.test(line) || /max-age=0/i.test(line)) jar.delete(name);
    else if (value !== "") jar.set(name, value);
  }
};

/** Drive the real provider redirect, authorization grant and `handlers.GET`
 * callback — exactly the HTTP a browser would carry, minus the network. */
async function completeProviderRedirect(authorizeUrl: string) {
  const granted = await fetch(authorizeUrl, { redirect: "manual" });
  expect(granted.status).toBe(302);
  const callback = granted.headers.get("Location") ?? "";
  expect(callback).toMatch(/^http:\/\/localhost\/api\/auth\/callback\//);
  const response = await handlers.GET(new Request(callback, {
    headers: { cookie: cookieHeader(), host: "localhost", "x-forwarded-proto": "http" },
  }) as never);
  applyCookies(response);
  expect(response.status).toBe(302);
  return response;
}

/** An ordinary retained-session sign-in through the real provider path. */
async function signInOrdinarily(provider: "discord" | "telegram") {
  current.headers = appHeaders();
  const authorize = await signIn(provider, { redirect: false, redirectTo: "/zh-Hant/today" });
  expect(typeof authorize).toBe("string");
  const response = await completeProviderRedirect(authorize);
  expect(response.headers.get("Location")).toBe("http://localhost/zh-Hant/today");
  expect(jar.has("authjs.session-token")).toBe(true);
}

async function consentEntry(action = "approve") {
  const request = new Request("http://localhost/api/v1/holder/link-consent", {
    method: "POST",
    headers: { Origin: "http://localhost", "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: (shared.control.link ?? {}).request_id, action }),
  });
  current.headers = appHeaders();
  const response = await POST(request);
  expect(response.status).toBe(303);
  return response.headers.get("Location") ?? "";
}
const decisions = () => shared.control.calls.filter((c) => c.path.endsWith("/decisions"));

describe("a holder consenting through a fresh provider callback", () => {
  it("approves through Discord after an ordinary login that submits nothing", async () => {
    await signInOrdinarily("discord");
    expect(decisions()).toHaveLength(0);
    const authorize = await consentEntry();
    expect(authorize).toContain("/authorize");
    expect(authorize).toContain("prompt=consent");
    expect(jar.has("nora-link-consent")).toBe(true);
    const callback = await completeProviderRedirect(authorize);
    expect(shared.control.prompt).toBe("consent");
    expect(callback.headers.get("Location")).toBe("http://localhost/api/v1/holder/link-consent");
    current.headers = appHeaders();
    const result = await GET();
    expect(result.status).toBe(200);
    expect(await result.json()).toEqual({ status: "ok",
      request_id: "generated-request", action: "approve", state: "pending" });
    expect(decisions()).toHaveLength(1);
    const decision = decisions()[0];
    expect(decision.claims.purpose).toBe("sign_in");
    expect(decision.claims.binding).toBeUndefined();
    expect(decision.claims.account).toEqual(
      { platform: "discord", kind: "user_id", value: "generated-discord-a" });
    expect(decision.claims.authentication_method).toBe("discord_oauth");
    expect(decision.claims.authenticated_at).toBeGreaterThanOrEqual(
      Math.floor(Date.now() / 1000) - 60);
    expect(JSON.parse(decision.body)).toEqual({ operation_id: expect.any(String),
      action: "approve", nonce: "generated-private-nonce" });
    // The intent cookie was consumed by the result read.
    expect(jar.has("nora-link-consent")).toBe(false);
    expect((await GET()).status).toBe(403);
  });

  it("refuses when the fresh Discord callback proves a different account", async () => {
    await signInOrdinarily("discord");
    const authorize = await consentEntry();
    // The provider now authenticates a different account than the intent bound.
    shared.control.discordId = "generated-discord-b";
    const callback = await completeProviderRedirect(authorize);
    expect(callback.headers.get("Location")).toBe("http://localhost/api/v1/holder/link-consent");
    expect(jar.has("authjs.session-token")).toBe(true);
    current.headers = appHeaders();
    const result = await GET();
    expect(result.status).toBe(403);
    expect(await result.json()).toEqual({ status: "denied",
      request_id: "generated-request", action: "approve" });
    // No decision ever reached the private service.
    expect(decisions()).toHaveLength(0);
  });

  it("approves through the Telegram OIDC path with discovery and a signed id token", async () => {
    shared.control.link = telegramLink;
    await signInOrdinarily("telegram");
    expect(decisions()).toHaveLength(0);
    const authorize = await consentEntry();
    expect(authorize).toContain("/auth");
    expect(authorize).not.toContain("prompt=consent");
    const callback = await completeProviderRedirect(authorize);
    expect(callback.headers.get("Location")).toBe("http://localhost/api/v1/holder/link-consent");
    current.headers = appHeaders();
    const result = await GET();
    expect(result.status).toBe(200);
    expect(await result.json()).toEqual({ status: "ok",
      request_id: "generated-request-t", action: "approve", state: "pending" });
    const decision = decisions()[0];
    expect(decision.claims.purpose).toBe("sign_in");
    expect(decision.claims.authentication_method).toBe("telegram_oidc");
    expect(decision.claims.account).toEqual(
      { platform: "telegram", kind: "user_id", value: "427311" });
  });
});
