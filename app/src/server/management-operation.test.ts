import { createHash, createHmac } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const login = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ auth: async () => login.current }));
const { POST } = await import("@/app/api/v1/management/operations/route");
const key = "generated-authentication-key-for-local-contracts";
const identity = {
  authentication: { sessionId: "generated-session", account: { platform: "discord",
    kind: "user_id", value: "generated-account" }, method: "discord_oauth",
  proofIssuer: "generated-client", authenticatedAt: Math.floor(Date.now() / 1000) },
  binding: { person_id: "generated-person", person_revision: "1",
    account_id: "generated-binding", account_revision: "2" },
};
let server: Server;
let calls: number;
let code: number;
let result: unknown;

beforeEach(async () => {
  calls = 0;
  code = 200;
  result = { status: "complete" };
  login.current = { user: { managedIdentity: identity } };
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
  vi.stubEnv("NORA_AUTHENTICATION_ISSUER", "generated-portal");
  vi.stubEnv("AUTH_URL", "https://generated.invalid");
  server = createServer(async (request, response) => {
    calls++;
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    if (request.url !== "/app/v1/management/operations") {
      response.writeHead(404).end();
      return;
    }
    const [header, payload, signature] =
      (request.headers.authorization ?? "").replace(/^Nora-Authentication /, "").split(".");
    expect(signature).toBe(createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url"));
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
    expect(claims.path).toBe("/app/v1/management/operations");
    expect(claims.method).toBe("POST");
    expect(claims.body_sha256).toBe(createHash("sha256").update(body).digest("hex"));
    expect(claims.binding).toEqual(identity.binding);
    expect(claims.purpose).toBe("session");
    expect(claims).not.toHaveProperty("actor");
    if (code === 307) response.writeHead(code, { Location: "/other" }).end();
    else response.writeHead(code, { "Content-Type": "application/json" }).end(JSON.stringify(result));
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
const request = (origin: string | null = "https://generated.invalid") => new Request(
  "https://generated.invalid/api/v1/management/operations", {
    method: "POST", body: JSON.stringify({ operation_id: "generated-operation",
      kind: "set_membership", target: { kind: "membership", person_id: "generated-member",
        scope: { kind: "room", id: "generated-room" } }, payload: { member: true },
      observed_revisions: { authority: "observed" } }),
    headers: origin ? { Origin: origin } : {},
  });

it("uses the verified server session to sign the exact mutation request", async () => {
  const response = await POST(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ status: "complete" });
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  expect(calls).toBe(1);
});
it("refuses cross-origin requests, absent sessions and file mode before forwarding", async () => {
  for (const origin of [null, "https://foreign.invalid"]) {
    expect((await POST(request(origin))).status).toBe(403);
  }
  login.current = null;
  expect((await POST(request())).status).toBe(401);
  vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
  expect((await POST(request())).status).toBe(403);
  expect(calls).toBe(0);
});
it("preserves refusal, incomplete publication and unavailability", async () => {
  for (const [status, body] of [[403, "denied"], [409, "incomplete"], [503, "unavailable"]] as const) {
    code = status;
    result = { status: body };
    const response = await POST(request());
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(result);
  }
});
it("does not claim completion for a malformed, contradictory or redirected reply", async () => {
  for (const body of [{ status: "complete", actor: "unexpected" }, { status: "denied" }, {}]) {
    result = body;
    expect((await POST(request())).status).toBe(503);
  }
  code = 307;
  expect((await POST(request())).status).toBe(503);
  expect(calls).toBe(4);
});
