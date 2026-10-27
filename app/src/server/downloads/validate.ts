// What a submitted URL may be (cc-downloader's rules, kept): http or https, and
// never a private, loopback or link-local address — a download makes THIS server
// fetch the URL, so an internal name would be SSRF from inside the compose
// network. The check is on the URL's literal hostname; a public name that
// resolves to a private address is the residual hole DNS pinning would close.
// hostname arrives bracketed for IPv6 literals ("[::1]"); strip the brackets
// before testing so the anchors below apply to either form.
const PRIVATE_HOST =
  /^(?:localhost|127\.|0\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.|::1|fc|fd|fe80|::)/i;

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
  if (PRIVATE_HOST.test(parsed.hostname.replace(/^\[|\]$/g, ""))) {
    return { ok: false, error: "private" };
  }
  return { ok: true, url: parsed.toString() };
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
