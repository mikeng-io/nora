import { afterEach, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const authentication = vi.hoisted(() => ({ capture: vi.fn() }));
vi.mock("@/server/management-session", async (original) => ({
  ...await original<typeof import("./server/management-session")>(),
  captureManagedSession: authentication.capture,
}));

const consent = vi.hoisted(() => ({ apply: vi.fn() }));
vi.mock("@/server/link-consent", async (original) => ({
  ...await original<typeof import("./server/link-consent")>(),
  applyConsentIntent: consent.apply,
}));

const captured = vi.hoisted(() => ({ config: null as unknown }));
vi.mock("next-auth", () => ({ default: (config: unknown) => {
  captured.config = config;
  return {};
} }));
vi.mock("./discord", () => ({ discordProvider: { id: "discord" } }));
vi.mock("@/server/otp", () => ({ codeSignInEnabled: () => false }));
await import("./auth");

const callbackRequest = new Request(
  "https://portal.generated.invalid/api/auth/callback/discord?state=generated-flow");
const { callbacks } = (captured.config as (request?: Request) => unknown)(callbackRequest) as { callbacks: {
  jwt: (args: { token: Record<string, unknown>; account?: { provider: string;
    providerAccountId?: string }; user?: { id: string; managedIdentity?: unknown } }) => Promise<Record<string, unknown>>;
  session: (args: { session: { user: Record<string, unknown> }; token: Record<string, unknown> }) => Promise<{ user: Record<string, unknown> }>;
} };
afterEach(() => { vi.unstubAllEnvs(); authentication.capture.mockReset();
  consent.apply.mockReset(); });

it("gives every sign-in a distinct generation, retaining it through session reads", async () => {
  vi.stubEnv("NORA_IDENTITY_BACKEND", "file");
  const first = await callbacks.jwt({ token: {}, account: { provider: "otp" }, user: { id: "person-a" } });
  const next = await callbacks.jwt({ token: {}, account: { provider: "otp" }, user: { id: "person-a" } });
  expect(first.sessionId).toEqual(expect.any(String));
  expect(next.sessionId).not.toBe(first.sessionId);
  const read = await callbacks.jwt({ token: first });
  expect(read.sessionId).toBe(first.sessionId);
  const session = await callbacks.session({ session: { user: {} }, token: read });
  expect(session.user.sessionId).toBe(first.sessionId);
});

it("captures managed authentication only at a verified provider callback", async () => {
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  vi.stubEnv("AUTH_DISCORD_ID", "generated-client");
  authentication.capture.mockImplementation(async (event) => ({ authentication: event,
    binding: { person_id: "generated-person", person_revision: "1",
      account_id: "generated-binding", account_revision: "2" } }));
  consent.apply.mockImplementation(async () => ({ requestId: "generated-request",
    action: "approve", operationId: "generated-operation", outcome: "complete", at: 1 }));
  const token = await callbacks.jwt({ token: { personId: "invented", discordId: "invented" },
    account: { provider: "discord", providerAccountId: "generated-account" },
    user: { id: "untrusted-display-id" } });
  expect(authentication.capture).toHaveBeenCalledWith(expect.objectContaining({
    account: { platform: "discord", kind: "user_id", value: "generated-account" },
    proofIssuer: "generated-client", method: "discord_oauth",
  }));
  expect(token).not.toHaveProperty("personId");
  expect(token).not.toHaveProperty("discordId");
  // The consent hook runs only where `account` is a genuine fresh provider
  // event — the retained token below cannot reach it again.
  expect(consent.apply).toHaveBeenCalledOnce();
  expect(consent.apply).toHaveBeenCalledWith(expect.objectContaining({
    account: { platform: "discord", kind: "user_id", value: "generated-account" },
    method: "discord_oauth", proofIssuer: "generated-client",
  }), "generated-flow");
  expect(token.linkConsent).toMatchObject({ outcome: "complete",
    requestId: "generated-request" });
  const read = await callbacks.jwt({ token });
  expect(read).toEqual(token);
  expect(authentication.capture).toHaveBeenCalledOnce();
  expect(consent.apply).toHaveBeenCalledOnce();
  const session = await callbacks.session({ session: { user: {} }, token });
  expect(session.user.managedIdentity).toEqual(token.managedIdentity);
  expect(session.user.linkConsent).toEqual(token.linkConsent);
});

it("never turns a person-only code or development identity into managed account proof", async () => {
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  for (const provider of ["otp", "dev"]) {
    await expect(callbacks.jwt({ token: {}, account: { provider },
      user: { id: "generated-person" } })).rejects.toThrow("account-exact authentication");
  }
  expect(authentication.capture).not.toHaveBeenCalled();
  expect(consent.apply).not.toHaveBeenCalled();
});

it("retains the code service's captured exact binding without recapturing a person-only session", async () => {
  vi.stubEnv("NORA_IDENTITY_BACKEND", "managed");
  const managedIdentity = { authentication: { sessionId: "otp:generated-challenge",
    account: { platform: "signal", kind: "phone", value: "+85290000101" },
    method: "signal_code", proofIssuer: "nora-otp", authenticatedAt: Math.floor(Date.now() / 1000) },
  binding: { person_id: "generated-person", person_revision: "0",
    account_id: "generated-account", account_revision: "0" } };
  const token = await callbacks.jwt({ token: {}, account: { provider: "otp" },
    user: { id: "generated-person", managedIdentity } });
  expect(token.managedIdentity).toEqual(managedIdentity);
  expect(token.sessionId).toBe("otp:generated-challenge");
  expect(authentication.capture).not.toHaveBeenCalled();
  expect(consent.apply).not.toHaveBeenCalled();
});
