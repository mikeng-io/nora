import './provider-diagnostics.mjs';
import { randomUUID } from 'node:crypto';

// Ollama reuses completion IDs; LangGraph treats them as message identities,
// so a collision replaces an earlier assistant message and can stop a worker.
// ponytail: CI uses JSON responses. Remove this shim when the provider supplies
// unique IDs; streaming needs its own adapter if it is enabled later.
const fetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const response = await fetch(input, init);
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (url.hostname !== 'ollama.com' || url.pathname !== '/v1/chat/completions'
      || !response.ok || !response.headers.get('content-type')?.includes('application/json')) return response;
  let body;
  try { body = await response.clone().json(); } catch { return response; }
  if (typeof body?.id !== 'string' || !Array.isArray(body.choices)) return response;
  body.id = `chatcmpl-${randomUUID()}`;
  const headers = new Headers(response.headers);
  // fetch has already decoded the body; its original byte count/encoding no
  // longer describes the reconstructed JSON response.
  headers.delete('content-length');
  headers.delete('content-encoding');
  headers.delete('transfer-encoding');
  return new Response(JSON.stringify(body), {
    status: response.status, statusText: response.statusText, headers,
  });
};
