/** Shared HTTP plumbing for bridge.js and the contract routes. */

export function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  res.end(body);
  return true;
}

export function readBody(req, maxBytes = 100 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

/**
 * Read a raw body bounded by `maxBytes` without dropping the connection: past
 * the bound the rest is drained and discarded, so the caller can still answer.
 * Resolves `{ data, over }`; `data` is null when `over`.
 */
export function readBoundedBody(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size <= maxBytes) chunks.push(chunk);
    });
    req.on('end', () => resolve(size > maxBytes
      ? { data: null, over: true } : { data: Buffer.concat(chunks), over: false }));
    req.on('error', reject);
  });
}
