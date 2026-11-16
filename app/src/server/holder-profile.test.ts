import { createHash, createHmac } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const login = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ auth: async () => login.current }));
const { POST } = await import("@/app/api/v1/holder/profile/route");
const key = "generated-holder-authentication-key-for-local-contracts";
const identity = {
  authentication: { sessionId: "generated-session", account: { platform: "discord",
    kind: "user_id", value: "generated-holder" }, method: "discord_oauth",
    proofIssuer: "generated-client", authenticatedAt: 1_790_000_000 },
  binding: { person_id: "generated-person", person_revision: "1",
    account_id: "generated-binding", account_revision: "2" },
};
let server: Server;
let calls: { path: string; body: string; purpose: string }[];
let code: number;
let result: unknown;
let replayBackend: boolean;
let effects: Map<string, string>;
let writes: number;

beforeEach(async () => {
  calls = [];
  code = 200;
  result = { status: "ok" };
  replayBackend = false;
  effects = new Map();
  writes = 0;
  login.current = { user: { managedIdentity: identity } };
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
  vi.stubEnv("AUTH_URL", "https://generated.invalid");
  server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    const [header, payload, signature] =
      (request.headers.authorization ?? "").replace(/^Nora-Authentication /, "").split(".");
    expect(signature).toBe(createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url"));
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
    expect(claims.path).toBe(request.url);
    expect(claims.method).toBe(request.method);
    expect(claims.body_sha256).toBe(createHash("sha256").update(body).digest("hex"));
    expect(claims.binding).toEqual(identity.binding);
    expect(claims.account).toEqual(identity.authentication.account);
    calls.push({ path: request.url ?? "", body, purpose: claims.purpose });
    if (replayBackend) {
      const intent = JSON.parse(body) as { operation_id: string; display_name: string };
      const previous = effects.get(intent.operation_id);
      if (previous !== undefined && previous !== intent.display_name) {
        response.writeHead(403, { "Content-Type": "application/json" })
          .end(JSON.stringify({ status: "denied", detail: "operation-conflict" }));
      } else {
        if (previous === undefined) {
          effects.set(intent.operation_id, intent.display_name);
          writes++;
        }
        response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({
          status: "ok", ...(previous === undefined ? {} : { detail: "replayed" }),
        }));
      }
      return;
    }
    if (code === 307) response.writeHead(code, { Location: "/unexpected" }).end();
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

function request(body?: unknown, origin: string | null = "https://generated.invalid") {
  return new Request("https://generated.invalid/api/v1/holder/profile", {
    method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    headers: origin ? { Origin: origin } : {},
  });
}

it("signs the profile path with the retained account and forwards the body", async () => {
  const body = { operation_id: "generated-operation", display_name: "Generated Name" };
  const response = await POST(request(body));
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ status: "ok" });
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  expect(calls.map((call) => call.path)).toEqual(["/app/v1/holder/profile"]);
  expect(calls[0].purpose).toBe("session");
  expect(calls[0].body).toBe(JSON.stringify(body));
});

it("refuses foreign origins, missing identity and file mode without forwarding", async () => {
  for (const origin of [null, "https://foreign.invalid"]) {
    expect((await POST(request({ operation_id: "x", display_name: "y" }, origin))).status).toBe(403);
  }
  login.current = null;
  expect((await POST(request({ operation_id: "x", display_name: "y" }))).status).toBe(401);
  login.current = { user: { managedIdentity: identity } };
  vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
  expect((await POST(request({ operation_id: "x", display_name: "y" }))).status).toBe(403);
  expect(calls).toHaveLength(0);
});

it("keeps a completed same-effect retry successful without a second effect", async () => {
  replayBackend = true;
  const body = { operation_id: "generated-replay", display_name: "Generated Name" };
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await POST(request(body));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
  }
  expect(calls).toHaveLength(2);
  expect(writes).toBe(1);
  expect(effects).toEqual(new Map([[body.operation_id, body.display_name]]));
  const conflict = await POST(request({ ...body, display_name: "Generated Other" }));
  expect(conflict.status).toBe(403);
  expect(writes).toBe(1);
  expect(effects.get(body.operation_id)).toBe(body.display_name);
});

it("preserves denied, unavailable and incomplete outcomes without reporting completion", async () => {
  for (const [status, label] of [[400, "denied"], [401, "denied"], [403, "denied"],
    [503, "unavailable"], [409, "incomplete"]] as const) {
    code = status;
    result = { status: label };
    const response = await POST(request({ operation_id: "x", display_name: "y" }));
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ status: label });
  }
});

it("refuses malformed, contradictory and redirected replies", async () => {
  for (const body of [{}, { status: "ok", display_name: "leak" }, { status: "denied" },
    { status: "unavailable" }]) {
    result = body;
    expect((await POST(request({ operation_id: "x", display_name: "y" }))).status).toBe(503);
  }
  code = 307;
  expect((await POST(request({ operation_id: "x", display_name: "y" }))).status).toBe(503);
  expect(calls).toHaveLength(5);
});
