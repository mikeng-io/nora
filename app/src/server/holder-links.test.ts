import { createHash, createHmac } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const login = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/auth", () => ({ auth: async () => login.current }));
const { GET, POST } = await import("@/app/api/v1/holder/[...path]/route");
const key = "generated-holder-authentication-key-for-local-contracts";
const identity = {
  authentication: { sessionId: "generated-session", account: { platform: "discord",
    kind: "user_id", value: "generated-initiator" }, method: "discord_oauth",
    proofIssuer: "generated-client", authenticatedAt: 1_790_000_000 },
  binding: { person_id: "generated-person", person_revision: "1",
    account_id: "generated-binding", account_revision: "2" },
};
const link = {
  request_id: "generated-request", side: "initiating", state: "pending",
  pair: { initiating: identity.authentication.account,
    receiving: { platform: "signal", kind: "uuid", value: "generated-receiver" } },
  surviving_person: "generated-person", created_at: "2026-11-01T00:00:00+00:00",
  expires_at: "2026-11-01T00:15:00+00:00", approved: [],
  challenge: { nonce: "generated-private-nonce", approve: "generated-approve", refuse: "generated-refuse" },
};
let server: Server;
let calls: { path: string; body: string; purpose: string }[];
let code: number;
let result: unknown;

beforeEach(async () => {
  calls = [];
  code = 200;
  result = { status: "ok", request: link };
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

function request(method: string, path: string[], body?: unknown, origin: string | null = "https://generated.invalid") {
  return [new Request(`https://generated.invalid/api/v1/holder/${path.join("/")}`, {
    method, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    headers: origin ? { Origin: origin } : {},
  }), { params: Promise.resolve({ path }) }] as const;
}

it("signs actual create, private view and unlink paths from the retained account", async () => {
  const body = { request_id: link.request_id, receiving: link.pair.receiving,
    surviving_person: "generated-person" };
  const created = await POST(...request("POST", ["link-requests"], body));
  expect(created.status).toBe(200);
  expect(await created.json()).toEqual({ status: "ok", request: link });
  const read = await GET(...request("GET", ["link-requests", link.request_id]));
  expect(read.status).toBe(200);
  expect(read.headers.get("Cache-Control")).toBe("private, no-store");
  result = { status: "ok" };
  expect((await POST(...request("POST", ["unlink"], {
    operation_id: "generated-operation", expected_person: "generated-person" }))).status).toBe(200);
  expect(calls.map((call) => call.path)).toEqual(["/app/v1/holder/link-requests",
    "/app/v1/holder/link-requests/generated-request", "/app/v1/holder/unlink"]);
  expect(calls.every((call) => call.purpose === "session")).toBe(true);
  expect(calls[0].body).toBe(JSON.stringify(body));
});

it("refuses consent and path injection instead of turning a session into approval", async () => {
  for (const path of [["link-requests", "generated-request", "decisions"], ["..", "management"],
    ["link-requests", "foreign/decisions"], ["link-requests", "?request=foreign"]]) {
    expect((await POST(...request("POST", path, {}))).status).toBe(404);
    expect((await GET(...request("GET", path))).status).toBe(404);
  }
  expect(calls).toHaveLength(0);
});

it("refuses foreign origins, missing identity and file mode without forwarding", async () => {
  for (const origin of [null, "https://foreign.invalid"]) {
    expect((await POST(...request("POST", ["unlink"], {}, origin))).status).toBe(403);
  }
  login.current = null;
  expect((await GET(...request("GET", ["link-requests", link.request_id]))).status).toBe(401);
  vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
  expect((await POST(...request("POST", ["link-requests"], {}))).status).toBe(403);
  expect(calls).toHaveLength(0);
});

it("preserves denied, unavailable and partial outcomes without reporting completion", async () => {
  for (const [status, label] of [[400, "denied"], [401, "denied"], [403, "denied"],
    [503, "unavailable"], [409, "incomplete"]] as const) {
    code = status;
    result = { status: label };
    const response = await POST(...request("POST", ["unlink"], {}));
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ status: label });
  }
});

it("refuses malformed, contradictory, wrong-request and redirected replies", async () => {
  for (const body of [{}, { status: "ok" }, { status: "denied" },
    { status: "ok", request: { ...link, request_id: "another-request" } },
    { status: "ok", request: { ...link, side: "receiving" } },
    { status: "ok", request: { ...link, challenge: { nonce: "foreign", otherSide: "leak" } } }]) {
    result = body;
    expect((await GET(...request("GET", ["link-requests", link.request_id]))).status).toBe(503);
  }
  code = 307;
  expect((await GET(...request("GET", ["link-requests", link.request_id]))).status).toBe(503);
  expect(calls).toHaveLength(7);
});
