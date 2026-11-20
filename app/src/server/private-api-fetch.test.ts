import { createHash, createHmac } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const login = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ auth: async () => login.current }));
const { privateApiFetch } = await import("./private-api-fetch");
const key = "generated-authentication-key-for-local-contracts";
const identity = {
  authentication: { sessionId: "generated-session", account: { platform: "discord",
    kind: "user_id", value: "generated-account" }, method: "discord_oauth",
    proofIssuer: "generated-client", authenticatedAt: Math.floor(Date.now() / 1000) },
  binding: { person_id: "generated-person", person_revision: "1",
    account_id: "generated-binding", account_revision: "2" },
};
let server: Server;
let base: string;
let calls: number;
let received: { body: string; proof: Record<string, unknown> };

beforeEach(async () => {
  calls = 0;
  login.current = { user: { managedIdentity: identity } };
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
  server = createServer(async (request, response) => {
    calls++;
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    const [header, payload, signature] =
      (request.headers.authorization ?? "").replace(/^Nora-Authentication /, "").split(".");
    const proof = payload ? JSON.parse(Buffer.from(payload, "base64url").toString()) : {};
    received = { body, proof };
    if (process.env.NORA_IDENTITY_BACKEND === "managed") {
      expect(signature).toBe(createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url"));
      expect(proof.path).toBe(request.url);
      expect(proof.method).toBe(request.method);
      expect(proof.body_sha256).toBe(createHash("sha256").update(body).digest("hex"));
      expect(proof.binding).toEqual(identity.binding);
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "ok" }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  vi.stubEnv("NORA_API_BASE", base);
});

afterEach(async () => {
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  vi.unstubAllEnvs();
});

it("signs exact queries and document write bytes with the retained account", async () => {
  expect((await privateApiFetch(`${base}/app/v1/memory?personId=generated-person`)).status).toBe(200);
  expect(received.proof.purpose).toBe("session");
  const body = JSON.stringify({ state: "archived" });
  expect((await privateApiFetch(`${base}/app/v1/documents/generated/state?personId=generated-person`,
    { method: "POST", body, headers: { "Content-Type": "application/json" } })).status).toBe(200);
  expect(received.body).toBe(body);
  expect(calls).toBe(2);
});

it("refuses missing provenance and foreign destinations before network access", async () => {
  await expect(privateApiFetch("https://foreign.invalid/app/v1/memory")).rejects.toThrow();
  login.current = { user: { personId: "generated-person" } };
  await expect(privateApiFetch(`${base}/app/v1/memory`)).rejects.toThrow();
  expect(calls).toBe(0);
});

it("preserves file-backed requests without creating managed authentication", async () => {
  vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
  login.current = null;
  expect((await privateApiFetch(`${base}/app/v1/status`)).status).toBe(200);
  expect(calls).toBe(1);
  expect(received.proof).toEqual({});
});
