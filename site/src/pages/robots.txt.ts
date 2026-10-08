import type { APIRoute } from "astro";

// The site shares its hostname with the portal (site/README.md). This file
// answers for both. Only the two home pages are the site's; every other path
// is the portal's (signed-in pages under /zh-Hant/ and /en/, and the
// /artifacts/ links Nora sends).
export const GET: APIRoute = ({ site }) =>
  new Response(
    [
      "User-agent: *",
      "Disallow: /artifacts/",
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
