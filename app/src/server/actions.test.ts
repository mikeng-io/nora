import { beforeEach, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next-auth", () => ({ AuthError: class extends Error {} }));
vi.mock("@/auth", () => ({
  auth: vi.fn(), signOut: vi.fn(), signIn: vi.fn(), DEV_LOGIN_ENABLED: false,
}));
vi.mock("next/navigation", () => ({ redirect: vi.fn() }));
vi.mock("@/i18n/routing", () => ({ routing: { locales: ["en", "zh-Hant"], defaultLocale: "zh-Hant" } }));
import { auth, signOut } from "@/auth";
import { signOutAction } from "./actions";

beforeEach(() => vi.clearAllMocks());

it("rechecks the current session when a delayed receiver request reaches the server", async () => {
  // The receiver still holds session-a, but another tab has signed in again.
  vi.mocked(auth).mockResolvedValue({ user: { sessionId: "session-b" } } as never);
  await signOutAction("en", "session-a");
  expect(signOut).not.toHaveBeenCalled();
});

it("signs out a matching generation and still permits ordinary local sign-out", async () => {
  vi.mocked(auth).mockResolvedValue({ user: { sessionId: "session-a" } } as never);
  await signOutAction("en", "session-a");
  expect(signOut).toHaveBeenCalledWith({ redirectTo: "/en/login?signedOut=1" });
  vi.mocked(signOut).mockClear();
  await signOutAction("en");
  expect(signOut).toHaveBeenCalledOnce();
});

it("does not clear cookies for an unknown generation or an already ended session", async () => {
  for (const current of [{ user: {} }, null]) {
    vi.mocked(auth).mockResolvedValue(current as never);
    await signOutAction("en", "session-a");
    expect(signOut).not.toHaveBeenCalled();
  }
});
