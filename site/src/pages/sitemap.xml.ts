import type { APIRoute } from "astro";
import { sitemap } from "../seo";

export const GET: APIRoute = ({ site }) =>
  new Response(sitemap(site!), { headers: { "Content-Type": "application/xml; charset=utf-8" } });
