/**
 * n.example.net — Nora's link shortener (Cloudflare Worker + KV).
 *
 * Two routes, nothing else:
 *   GET  /{slug}      → 301 to the stored URL, 404 if unknown.
 *   POST /api/links   → mint. Bearer-authed (MINT_TOKEN, a Worker secret);
 *                       body {"url": "https://…"}; answers {slug, short_url}.
 *
 * Deliberately self-hosted: a third-party shortener would see every
 * destination the group routes to. Only https targets are accepted, and the
 * mint API is not for browsers — no CORS headers on purpose.
 */

// Base58-ish: no 0/O/I/l, so a slug survives being read aloud.
const ALPHABET = "123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ";
const SLUG_LEN = 7;

function randomSlug() {
  const bytes = new Uint8Array(SLUG_LEN);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += ALPHABET[b % ALPHABET.length];
  return out;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/links" && request.method === "POST") {
      const auth = request.headers.get("Authorization") || "";
      if (auth !== `Bearer ${env.MINT_TOKEN}`) {
        return json({ error: "forbidden" }, 403);
      }
      let target;
      try {
        target = new URL((await request.json()).url);
      } catch {
        return json({ error: "body must be {\"url\": \"https://…\"}" }, 400);
      }
      if (target.protocol !== "https:") {
        return json({ error: "https targets only" }, 400);
      }
      // Random slug, retry on the (astronomically rare) collision.
      for (let i = 0; i < 5; i++) {
        const slug = randomSlug();
        if ((await env.LINKS.get(slug)) === null) {
          await env.LINKS.put(slug, target.toString());
          return json({ slug, short_url: `https://${url.hostname}/${slug}` });
        }
      }
      return json({ error: "could not allocate a slug" }, 500);
    }

    if (request.method === "GET" && url.pathname.length > 1) {
      const target = await env.LINKS.get(url.pathname.slice(1));
      if (target) return Response.redirect(target, 301);
      return new Response("not found", { status: 404 });
    }

    return new Response("nrtx link service", { status: 200 });
  },
};
