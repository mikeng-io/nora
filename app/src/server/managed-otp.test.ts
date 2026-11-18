import { createHash, createHmac } from "node:crypto";
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const { verifyManagedCode } = await import("./managed-otp");
const key = "generated-authentication-key-for-local-contracts";
const binding = { person_id: "generated-person", person_revision: "0",
  account_id: "generated-account", account_revision: "0" };
const account = { platform: "signal", kind: "phone", value: "+85290000101" };
let server: Server;
let mode: "valid" | "person-only" | "drift" | "foreign-session";
let exchanges: number;
let spent: boolean;
beforeEach(async () => {
  mode = "valid"; exchanges = 0; spent = false;
  vi.stubEnv("NORA_AUTHENTICATION_KEY", key);
  vi.stubEnv("NORA_AUTHENTICATION_ISSUER", "generated-portal");
  vi.stubEnv("NORA_OTP_API_TOKEN", "generated-otp-service-key");
  server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const raw = Buffer.concat(chunks).toString();
    const body = JSON.parse(raw);
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/app/v1/otp/verify") {
      if (request.headers.authorization !== "Bearer generated-otp-service-key" || spent
        || body.challengeId !== "generated-challenge" || body.code !== "123456"
        || body.purpose !== "sign_in") {
        response.writeHead(401).end(JSON.stringify({ error: "refused" })); return;
      }
      spent = true;
      response.end(JSON.stringify({ personId: binding.person_id,
        ...(mode === "person-only" ? {} : { managedIdentity: { binding, authentication: {
          sessionId: mode === "foreign-session" ? "foreign" : "otp:generated-challenge",
          account, method: "signal_code", proofIssuer: "nora-otp",
          authenticatedAt: Math.floor(Date.now() / 1000),
        } } }),
      })); return;
    }
    if (request.url !== "/app/v1/management/auth/exchange") {
      response.writeHead(404).end(); return;
    }
    exchanges++;
    const [header, payload, signature] = (request.headers.authorization ?? "")
      .replace(/^Nora-Authentication /, "").split(".");
    expect(signature).toBe(createHmac("sha256", key).update(`${header}.${payload}`).digest("base64url"));
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
    expect(claims.method).toBe("POST"); expect(claims.path).toBe(request.url);
    expect(claims.body_sha256).toBe(createHash("sha256").update(raw).digest("hex"));
    expect(claims.binding).toEqual(binding); expect(claims.account).toEqual(account);
    expect(claims.authentication_method).toBe("signal_code"); expect(claims.purpose).toBe("sign_in");
    expect(claims).not.toHaveProperty("actor");
    if (mode === "drift") response.writeHead(403).end(JSON.stringify({ status: "denied" }));
    else response.end(JSON.stringify({ status: "ok", binding, display_name: "Generated person" }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No local listener");
  vi.stubEnv("NORA_API_BASE", `http://127.0.0.1:${address.port}`);
});
afterEach(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve())); vi.unstubAllEnvs();
});
const verify = (code = "123456") => verifyManagedCode({ challengeId: "generated-challenge", code });
it("exchanges one verified exact-account receipt with its original binding", async () => {
  expect(await verify("000000")).toBeNull();
  const session = await verify();
  expect(session?.binding).toEqual(binding); expect(session?.authentication.method).toBe("signal_code");
  expect(await verify()).toBeNull(); expect(exchanges).toBe(1);
});
it("refuses a person-only or another challenge's receipt", async () => {
  mode = "person-only"; expect(await verify()).toBeNull();
  spent = false; mode = "foreign-session"; expect(await verify()).toBeNull(); expect(exchanges).toBe(0);
});
it("cannot refresh the receipt binding when identity changes before exchange", async () => {
  mode = "drift";
  await expect(verify()).rejects.toThrow("Managed identity authentication failed");
  expect(exchanges).toBe(1);
});
