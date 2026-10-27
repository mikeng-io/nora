import "server-only";
import dns from "node:dns/promises";

// What a submitted URL may be: http or https, and never a private, loopback,
// link-local or internal address — a download makes THIS server fetch the URL,
// so anything internal is SSRF from inside the compose network (a circle member
// could ask for http://nora:9100/status and read it back from the shelf).
// The boundary is enforced twice: the literal hostname here, and the resolved
// addresses in assertPublicHost() — a public name that resolves privately is
// refused at submit and again when the job runs (DNS can be rebound between
// them). Residual holes, named rather than silently missing: yt-dlp follows
// HTTP redirects inside its own process (a public URL that 302s to an internal
// address is not pinned), and it resolves the host again on its own clock, so
// a name that flips answers between our check and its fetch (rebinding) is not
// pinned either. Closing both needs an egress proxy, not a hostname check.

// hostname arrives bracketed for IPv6 literals ("[::1]"); strip the brackets
// before testing so the anchors below apply to either form.
const PRIVATE_HOST =
  /^(?:localhost|127\.|0\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|169\.254\.|::|fc|fd|fe[89ab])/i;
const INTERNAL_TLD = /\.(?:internal|local|lan|home|corp|localhost|arpa)$/i;

export function validateSubmissionUrl(url: string): { ok: true; url: string } | { ok: false; error: string } {
  const trimmed = url.trim();
  if (!trimmed) return { ok: false, error: "empty" };
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return { ok: false, error: "invalid" };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, error: "scheme" };
  }
  const host = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (PRIVATE_HOST.test(host) || isPrivateAddress(host)) {
    return { ok: false, error: "private" };
  }
  // IP literals are done: a public v6 literal like [2001:4860:4860::8888] has
  // no dot but is not a service name. The single-label/TLD rules below judge
  // DNS names only — a bare "nora" is a compose service, never a site.
  const looksLikeIp = host.includes(":") || /^\d+\.\d+\.\d+\.\d+$/.test(host);
  if (!looksLikeIp && (!host.includes(".") || INTERNAL_TLD.test(host))) {
    return { ok: false, error: "private" };
  }
  return { ok: true, url: parsed.toString() };
}

/** One resolved IP (v4 or v6) is internal. Fail-closed on anything ambiguous. */
export function isPrivateAddress(addr: string): boolean {
  const a = addr.replace(/^\[|\]$/g, "").split("%")[0].toLowerCase();
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(a);
  if (v4) {
    const [a1, a2] = [Number(v4[1]), Number(v4[2])];
    if (a1 === 0 || a1 === 10 || a1 === 127) return true; // this-net, private, loopback
    if (a1 === 169 && a2 === 254) return true; // link-local
    if (a1 === 172 && a2 >= 16 && a2 <= 31) return true;
    if (a1 === 192 && a2 === 168) return true;
    if (a1 === 100 && a2 >= 64 && a2 <= 127) return true; // CGNAT / tailnets
    return a1 >= 224; // multicast + reserved
  }
  if (a === "::1" || a === "::" || a.startsWith("::")) return true; // loopback, unspecified, mapped
  if (/^fe[89ab]/.test(a)) return true; // fe80::/10 link-local
  return /^f[cd]/.test(a); // fc00::/7 unique-local
}

export type Lookup = (hostname: string) => Promise<{ address: string }[]>;

const defaultLookup: Lookup = async (hostname) => dns.lookup(hostname, { all: true, verbatim: true });

/** Resolve the URL's host and refuse when ANY answer is internal. A lookup that
 * fails means we cannot check, which is a refusal — never a pass (R-27). */
export async function assertPublicHost(
  url: string,
  lookup: Lookup = defaultLookup,
): Promise<{ ok: true } | { ok: false; error: string }> {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, error: "invalid" };
  }
  const host = parsed.hostname.replace(/^\[|\]$/g, "");
  // Literal IPs skip DNS (and must, so a race cannot swap the check).
  if (isPrivateAddress(host)) return { ok: false, error: "private" };
  let addrs: { address: string }[];
  try {
    addrs = await lookup(host);
  } catch {
    return { ok: false, error: "unresolvable" };
  }
  if (!addrs.length || addrs.some((a) => isPrivateAddress(a.address))) {
    return { ok: false, error: "private" };
  }
  return { ok: true };
}

/** The URLs a batch body may carry: non-empty lines / array entries, deduped,
 * each validated. Returns the accepted list plus per-URL rejections so the
 * caller can report which lines failed and why. */
export function validateBatch(urls: string[], maxBatch: number): {
  accepted: string[];
  rejected: { url: string; error: string }[];
  tooMany: boolean;
} {
  const seen = new Set<string>();
  const accepted: string[] = [];
  const rejected: { url: string; error: string }[] = [];
  for (const raw of urls) {
    const check = validateSubmissionUrl(raw);
    if (!check.ok) {
      rejected.push({ url: raw.trim().slice(0, 300), error: check.error });
      continue;
    }
    if (seen.has(check.url)) {
      rejected.push({ url: check.url, error: "duplicate" });
      continue;
    }
    seen.add(check.url);
    accepted.push(check.url);
  }
  return { accepted, rejected, tooMany: urls.length > maxBatch };
}
