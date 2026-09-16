// Log redaction: stream_urls are signed CDN locators whose query string (and
// any userinfo) is credential-ish — logs carry the URL with both stripped,
// and arbitrary diagnostic text (ffmpeg stderr) is URL-scrubbed the same way
// before it is allowed into a log line.

export function redactUrl(value) {
  if (typeof value !== 'string' || !value) return value ?? null;
  try {
    const url = new URL(value);
    url.search = '';
    url.username = '';
    url.password = '';
    return url.toString();
  } catch {
    return value
      .split('?')[0]
      .replace(/^(https?:\/\/)[^/@\s]+@/i, '$1');
  }
}

// Strip query strings and userinfo from every URL in free text; occurrences
// of a known secret URL are replaced first so a partial regex match cannot
// leave its query behind.
export function redactText(text, ...knownSecrets) {
  let out = String(text);
  for (const secret of knownSecrets) {
    if (secret) out = out.split(secret).join(redactUrl(secret));
  }
  return out.replace(/https?:\/\/[^\s"'()[\]<>]+/gi, (m) => redactUrl(m));
}
