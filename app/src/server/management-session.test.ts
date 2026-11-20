import { createHash, createHmac, randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const { authenticationAssertion, captureManagedSession, checkManagedSession, identityBackend } =
  await import("./management-session");

const key = "generated-authentication-key-for-local-contracts";
const binding = { person_id: "generated-person", person_revision: "1",
  account_id: "generated-account-binding", account_revision: "2" };
const event = () => ({ sessionId: randomUUID(), account: {
  platform: "discord" as const, kind: "user_id", value: "generated-account",
}, method: "discord_oauth" as const, proofIssuer: "generated-client",
authenticatedAt: Math.floor(Date.now() / 1000) });

async function listen(server: Server) {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing local listener");
  return `http://127.0.0.1:${address.port}`;
}

describe("managed authentication gateway", () => {
  let server: Server;
  let received: Record<string, unknown>[];
  let mode: "ok" | "deny" | "down" | "malformed" | "redirect";

  beforeEach(async () => {
    vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
    vi.stubEnv("NORA_AUTHENTICATION_ISSUER", "generated-portal");
    vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
    mode = "ok";
    received = [];
    server = createServer(async (request, response) => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const body = Buffer.concat(chunks).toString();
      const token = request.headers.authorization?.replace(/^Nora-Authentication /, "") ?? "";
      const [header, payload, signature] = token.split(".");
      const expected = createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url");
      if (!header || !payload || signature !== expected) {
        response.writeHead(401).end(JSON.stringify({ status: "denied" }));
        return;
      }
      const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
      expect(JSON.parse(Buffer.from(header, "base64url").toString())).toEqual({
        alg: "HS256", typ: "nora.authn+jwt", kid: "generated-portal",
      });
      expect(claims.path).toBe(request.url);
      expect(claims.method).toBe(request.method);
      expect(claims.body_sha256).toBe(createHash("sha256").update(body).digest("hex"));
      expect(claims.iss).toBe("generated-portal");
      expect(claims.aud).toBe("nora-managed-api");
      expect(claims.exp - claims.iat).toBe(60);
      expect(claims).not.toHaveProperty("actor");
      expect(claims).not.toHaveProperty("role");
      received.push(claims);
      response.setHeader("Content-Type", "application/json");
      if (mode === "redirect") {
        response.writeHead(307, { Location: "/app/v1/management/auth/check" }).end();
      } else if (mode === "deny") {
        response.writeHead(403).end(JSON.stringify({ status: "denied" }));
      } else if (mode === "down") {
        response.writeHead(503).end(JSON.stringify({ status: "unavailable" }));
      } else if (mode === "malformed") {
        response.writeHead(200).end(JSON.stringify({ status: "ok", binding: { person_id: "other" } }));
      } else if (request.url?.endsWith("/exchange")) {
        expect(claims.purpose).toBe("sign_in");
        expect(claims.binding).toBeUndefined();
        response.end(JSON.stringify({ status: "ok", binding, display_name: "Generated person" }));
      } else if (request.url?.endsWith("/check")) {
        expect(claims.purpose).toBe("session");
        if (JSON.stringify(claims.binding) !== JSON.stringify(binding)) {
          response.writeHead(403).end(JSON.stringify({ status: "denied" }));
        } else {
          response.end(JSON.stringify({ status: "ok", binding, display_name: "Generated person" }));
        }
      } else response.writeHead(404).end();
    });
    vi.stubEnv("NORA_API_BASE", await listen(server));
  });

  afterEach(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    vi.unstubAllEnvs();
  });

  it("captures and reuses the exact binding through real HTTP", async () => {
    const authentication = event();
    const session = await captureManagedSession(authentication);
    expect(session.binding).toEqual(binding);
    expect(await checkManagedSession(session)).toEqual({
      status: "ok", binding, displayName: "Generated person",
    });
    expect(received.map((claims) => claims.sub)).toEqual([
      authentication.sessionId, authentication.sessionId,
    ]);
  });

  it("does not silently refresh a retained revision", async () => {
    const session = await captureManagedSession(event());
    session.binding = { ...session.binding, account_revision: "1" };
    expect(await checkManagedSession(session)).toEqual({ status: "denied" });
  });

  it("preserves denial versus an unreadable or malformed authority", async () => {
    const session = await captureManagedSession(event());
    mode = "deny";
    expect(await checkManagedSession(session)).toEqual({ status: "denied" });
    mode = "down";
    expect(await checkManagedSession(session)).toEqual({ status: "unavailable" });
    mode = "malformed";
    expect(await checkManagedSession(session)).toEqual({ status: "unavailable" });
    await expect(captureManagedSession(event())).rejects.toThrow("Managed identity authentication failed");
  });

  it("refuses missing signing configuration and an unknown backend", async () => {
    vi.stubEnv("NORA_AUTHENTICATION_KEY", "");
    await expect(authenticationAssertion(event(), {
      purpose: "sign_in", path: "/test", method: "POST", body: "{}",
    })).rejects.toThrow("Authentication issuer is unavailable");
    vi.stubEnv("NORA_IDENTITY_BACKEND", "unknown");
    expect(identityBackend).toThrow("Invalid identity backend");
  });

  it("does not forward an authentication assertion through an HTTP redirect", async () => {
    const session = await captureManagedSession(event());
    mode = "redirect";
    expect(await checkManagedSession(session)).toEqual({ status: "unavailable" });
    expect(received).toHaveLength(2);
  });

  it("refuses reuse of the Auth.js or OTP service secret", async () => {
    for (const name of ["AUTH_SECRET", "NORA_OTP_API_TOKEN"]) {
      vi.stubEnv(name, key);
      await expect(captureManagedSession(event())).rejects.toThrow("authentication failed");
      vi.stubEnv(name, "generated-separate-secret");
    }
    expect(received).toHaveLength(0);
  });
});
