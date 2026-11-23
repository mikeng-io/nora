// Request bodies, read with their limits enforced on bytes as they arrive.

// Resolves { ok: true, raw, bytes } | { ok: false, reason: 'too_large' | 'timeout' | 'read_error' }.
// Past `limit` bytes the read goes on, to `hardLimit`, only when
// `mayExceed(prefix)` accepts the first `limit` bytes. An over-limit body is
// answered the moment it crosses the limit, without reading the rest. A body
// still arriving at the deadline is 'timeout'. A transport failure mid-body
// ('error', or 'close' before 'end') is a read error, not an over-limit body.
export function readBody(req, { limit, hardLimit = limit, mayExceed = () => false, timeoutMs }) {
  return new Promise((resolve) => {
    let size = 0;
    let settled = false;
    let allowed = false;
    const done = (result) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(result);
      }
    };
    // Explicit rather than Node's requestTimeout: that one does not fire while
    // a body is being consumed (measured on Node 26), so a slow-drip upload
    // would hold its socket until the peer gave up.
    const timer = setTimeout(() => done({ ok: false, reason: 'timeout' }), timeoutMs);
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (settled) return;
      chunks.push(chunk);
      // Judged on exactly the first `limit` bytes, however the chunks fell.
      if (size > limit && !allowed) allowed = mayExceed(Buffer.concat(chunks).subarray(0, limit).toString('utf8'));
      if ((size > limit && !allowed) || size > hardLimit) {
        req.pause();
        return done({ ok: false, reason: 'too_large' });
      }
    });
    req.on('end', () => done({ ok: true, raw: Buffer.concat(chunks).toString('utf8'), bytes: size }));
    req.on('error', () => done({ ok: false, reason: 'read_error' }));
    req.on('close', () => done({ ok: false, reason: 'read_error' }));
  });
}

// The value of the first top-level "kind" member of a JSON object, read from
// a prefix of it; null when the prefix does not show one. A caller sending a
// large body names its kind before its effect.
export function leadingKind(text) {
  let i = 0;
  const ws = () => { while (i < text.length && ' \t\n\r'.includes(text[i])) i += 1; };
  const string = () => { // at '"'; escapes are kept raw
    let out = '';
    for (i += 1; i < text.length; i += 1) {
      if (text[i] === '"') { i += 1; return out; }
      if (text[i] === '\\') { out += text[i]; i += 1; }
      out += text[i];
    }
    return null;
  };
  const skipValue = () => {
    if (text[i] === '"') return string() !== null;
    if (text[i] === '{' || text[i] === '[') {
      for (let depth = 0; i < text.length;) {
        if (text[i] === '"') {
          if (string() === null) return false;
          continue;
        }
        if (text[i] === '{' || text[i] === '[') depth += 1;
        else if (text[i] === '}' || text[i] === ']') depth -= 1;
        i += 1;
        if (depth === 0) return true;
      }
      return false;
    }
    while (i < text.length && !',}'.includes(text[i])) i += 1;
    return i < text.length;
  };
  ws();
  if (text[i] !== '{') return null;
  i += 1;
  for (;;) {
    ws();
    if (text[i] !== '"') return null;
    const key = string();
    ws();
    if (key === null || text[i] !== ':') return null;
    i += 1;
    ws();
    if (key === 'kind') return text[i] === '"' ? string() : null;
    if (!skipValue()) return null;
    ws();
    if (text[i] !== ',') return null;
    i += 1;
  }
}
