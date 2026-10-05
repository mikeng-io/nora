// One-time codes (DEC-287): the portal's half, kept apart from the route and the
// actions so it is tested without a server (otp.test.ts). Nora sends a six-digit code to
// a person's own Signal or WhatsApp account and checks it; this module only
// asks her to, server-side, with the bearer token her two routes require. The
// browser never sees the token or Nora's address.
//
// Off unless NORA_OTP_API_TOKEN is set, and then the login page offers it. Set
// the token here only once Nora runs with `[otp] enabled`: with the token and
// without the routes, every request answers "Nora couldn't send a code".

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

/** How often one visitor may ask for a code: 3 in 15 minutes. Nora's own caps
 * are per PERSON (5 an hour, 10 a day) and see one client, this portal; this
 * is the per-client half, so one script cannot spend somebody's whole day
 * of codes, or keep their phone buzzing, from one address.
 *
 * ponytail: in memory, per process: a restart forgets it and a second
 * replica would double it. One container serves the portal; a shared store is
 * the upgrade path if that changes. */
export class SendThrottle {
  private readonly seen = new Map<string, number[]>();
  constructor(
    private readonly limit = 3,
    private readonly windowMs = 15 * 60_000,
  ) {}

  allow(key: string, now: number): boolean {
    const since = now - this.windowMs;
    if (this.seen.size > 10_000) {
      for (const [k, times] of this.seen) if (times.every((t) => t <= since)) this.seen.delete(k);
    }
    const recent = (this.seen.get(key) ?? []).filter((t) => t > since);
    if (recent.length >= this.limit) {
      this.seen.set(key, recent);
      return false;
    }
    recent.push(now);
    this.seen.set(key, recent);
    return true;
  }
}

/** Who is asking, as the tunnel reports it: Cloudflare's own header first,
 * then the first proxy hop. One shared key when neither is present, which
 * throttles everybody together rather than nobody. */
export function clientKey(headers: { get(name: string): string | null }): string {
  return (
    headers.get("cf-connecting-ip")?.trim() ||
    headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    headers.get("x-real-ip")?.trim() ||
    "unknown"
  );
}
