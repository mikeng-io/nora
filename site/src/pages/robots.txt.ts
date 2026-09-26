import type { APIRoute } from "astro";

// The site shares its hostname with the portal and drop (site/README.md).
// Drop's own robots.txt says never index; this file answers for all three.
// Only the two home pages are the site's; every other path is the portal's
// (signed-in pages under /zh-Hant/ and /en/) or drop's.
export const GET: APIRoute = ({ site }) =>
  new Response(
    [
      "User-agent: *",
      "Disallow: /drop/",
      "Disallow: /i/",
      "Disallow: /api/",
      "Disallow: /zh-Hant/",
      "Disallow: /en/",
      "Allow: /en/$",
      "Allow: /",
      "",
      `Sitemap: ${new URL("/sitemap.xml", site).href}`,
      "",
    ].join("\n"),
    { headers: { "Content-Type": "text/plain; charset=utf-8" } },
  );
