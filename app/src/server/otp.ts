// One-time codes (DEC-287): the portal's half, kept apart from the route and the
// actions so it is tested without a server (otp.test.ts). Nora sends a six-digit code to
// a person's own Signal or WhatsApp account and checks it; this module only
// asks her to, server-side, with the bearer token her two routes require. The
// browser never sees the token or Nora's address.
//
// Off unless NORA_OTP_API_TOKEN is set: without it Nora has not mounted the
// routes either (`[otp] enabled`), so the login page shows Discord alone.

export type CodePlatform = "signal" | "whatsapp";
export const CODE_PLATFORMS: readonly CodePlatform[] = ["signal", "whatsapp"];

// Read when used rather than at import, so the server sees the container's
// environment and a test can set its own.
const token = () => process.env.NORA_OTP_API_TOKEN ?? "";
const base = () => process.env.NORA_API_BASE ?? "http://nora:9100";

export const codeSignInEnabled = () => token().length > 0;

type Fetch = typeof fetch;

async function post(path: string, body: unknown, fetchImpl: Fetch): Promise<Response | null> {
  if (!codeSignInEnabled()) return null;
  try {
    return await fetchImpl(`${base()}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token()}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return null;
  }
}

/** Ask Nora to send a code. The challenge id to check it against, or null
 * when Nora could not be reached. Nora answers the same whether or not the
 * number is somebody she knows, so a challenge id says nothing about that. */
export async function requestCode(
  { identifier, platform }: { identifier: string; platform: CodePlatform },
  fetchImpl: Fetch = fetch,
): Promise<string | null> {
  const res = await post(
    "/app/v1/otp/request", { identifier, platform, purpose: "sign_in" }, fetchImpl,
  );
  if (!res || res.status !== 202) return null;
  const body = (await res.json().catch(() => null)) as { challengeId?: unknown } | null;
  return typeof body?.challengeId === "string" ? body.challengeId : null;
}

/** The person a code was sent to, when it is right and still live; else
 * null, whatever the reason. */
export async function verifyCode(
  { challengeId, code }: { challengeId: string; code: string },
  fetchImpl: Fetch = fetch,
): Promise<string | null> {
  const res = await post(
    "/app/v1/otp/verify", { challengeId, code, purpose: "sign_in" }, fetchImpl,
  );
  if (!res || res.status !== 200) return null;
  const body = (await res.json().catch(() => null)) as { personId?: unknown } | null;
  return typeof body?.personId === "string" && body.personId ? body.personId : null;
}
