import { expect, it, vi } from "vitest";

const captured = vi.hoisted(() => ({ config: null as unknown }));
vi.mock("next-auth", () => ({ default: (config: unknown) => {
  captured.config = config;
  return {};
} }));
vi.mock("./discord", () => ({ discordProvider: { id: "discord" } }));
vi.mock("@/server/otp", () => ({ codeSignInEnabled: () => false }));
await import("./auth");

const { callbacks } = captured.config as { callbacks: {
  jwt: (args: { token: Record<string, unknown>; account?: { provider: string }; user?: { id: string } }) => Promise<Record<string, unknown>>;
  session: (args: { session: { user: Record<string, unknown> }; token: Record<string, unknown> }) => Promise<{ user: Record<string, unknown> }>;
} };

it("gives every sign-in a distinct generation, retaining it through session reads", async () => {
  const first = await callbacks.jwt({ token: {}, account: { provider: "otp" }, user: { id: "person-a" } });
  const next = await callbacks.jwt({ token: {}, account: { provider: "otp" }, user: { id: "person-a" } });
  expect(first.sessionId).toEqual(expect.any(String));
  expect(next.sessionId).not.toBe(first.sessionId);
  const read = await callbacks.jwt({ token: first });
  expect(read.sessionId).toBe(first.sessionId);
  const session = await callbacks.session({ session: { user: {} }, token: read });
  expect(session.user.sessionId).toBe(first.sessionId);
});
