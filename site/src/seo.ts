// What search engines and AI answer engines read about the site, derived from
// the same copy the page shows (src/i18n), so the two cannot drift: the
// JSON-LD graph in every home page's <head>, the sitemap and /llms.txt.
import { LOCALES, LOCALE_ORDER, type Locale } from "./i18n";

const AUTHOR = { name: "Miles Novak", url: "https://mikeng.io" };
const PLATFORMS = ["Signal", "WhatsApp", "Discord", "Instagram"];

/** schema.org graph for one home page. One `@id` per thing, shared by both
    languages, so the two pages describe the same Nora, site and author. */
export function structuredData(locale: Locale, site: URL): object {
  const info = LOCALES[locale];
  const t = info.dict;
  const at = (path: string) => new URL(path, site).href;
  const ids = { site: at("/#website"), nora: at("/#nora"), author: at("/#author") };
  const page = at(info.path);

  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": ids.site,
        url: at("/"),
        name: "Nora",
        inLanguage: LOCALE_ORDER.map((l) => LOCALES[l].lang),
        publisher: { "@id": ids.author },
      },
      {
        "@type": "WebPage",
        "@id": `${page}#webpage`,
        url: page,
        name: t.meta.title,
        description: t.meta.description,
        inLanguage: info.lang,
        isPartOf: { "@id": ids.site },
        about: { "@id": ids.nora },
        primaryImageOfPage: at(info.og),
      },
      {
        // An AI agent that lives in group chats. Not offered to the public
        // (only the circle she lives with can use her), so no `offers`.
        "@type": "SoftwareApplication",
        "@id": ids.nora,
        name: "Nora",
        url: at("/"),
        description: t.meta.description,
        applicationCategory: "CommunicationApplication",
        applicationSubCategory: "AI agent",
        operatingSystem: PLATFORMS.join(", "),
        image: at(info.og),
        creator: { "@id": ids.author },
      },
      { "@type": "Person", "@id": ids.author, name: AUTHOR.name, url: AUTHOR.url },
    ],
  };
}

/** One <url> per home page, each listing every language as an alternate. */
export function sitemap(site: URL): string {
  const alternates = LOCALE_ORDER.map(
    (l) =>
      `    <xhtml:link rel="alternate" hreflang="${LOCALES[l].lang}" href="${new URL(LOCALES[l].path, site).href}"/>`,
  );
  alternates.push(`    <xhtml:link rel="alternate" hreflang="x-default" href="${new URL("/", site).href}"/>`);
  const urls = LOCALE_ORDER.map((l) =>
    [`  <url>`, `    <loc>${new URL(LOCALES[l].path, site).href}</loc>`, ...alternates, `  </url>`].join("\n"),
  );
  return [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">`,
    ...urls,
    `</urlset>`,
    ``,
  ].join("\n");
}

/** /llms.txt (llmstxt.org): the page's claims as plain Markdown, English
    first, for a model that reads the site instead of rendering it. */
export function llmsTxt(site: URL): string {
  const t = LOCALES.en.dict;
  const zh = LOCALES["zh-Hant"].dict;
  const at = (path: string) => new URL(path, site).href;
  const list = (items: string[]) => items.map((i) => `- ${i}`);

  return [
    `# Nora`,
    ``,
    `> ${t.meta.description}`,
    ``,
    `Nora is private: she lives with one circle of friends and is not a product anyone can sign up for. ${t.portal.body}`,
    ``,
    `## How she works`,
    ``,
    `- **${t.loop.tagTitle}.** ${t.loop.tagBody}`,
    `- **${t.loop.ambientTitle}.** ${t.loop.ambientBody} ${t.loop.stages.map((s) => `${s.title}: ${s.body}`).join(" ")}`,
    `- ${t.loop.closing}`,
    ``,
    `## Memory`,
    ``,
    t.memory.lede,
    ``,
    ...list(t.memory.tiers.map((m) => `**${m.title}** (${m.scale}): ${m.body}`)),
    ``,
    t.memory.note,
    ``,
    `## Where she is, and what she can see`,
    ``,
    ...list(t.senses.rows.map((r) => `**${r.title}:** ${r.body}`)),
    ``,
    t.senses.note,
    ``,
    `## What she won't do`,
    ``,
    ...list(t.manners.items.map((m) => `**${m.title}** ${m.body}`)),
    ``,
    `## Her form`,
    ``,
    t.form.body.join("\n\n"),
    ``,
    `## Pages`,
    ``,
    `- [Nora (English)](${at(LOCALES.en.path)}): ${t.meta.description}`,
    `- [Nora (繁體中文, written Cantonese)](${at(LOCALES["zh-Hant"].path)}): ${zh.meta.description}`,
    ``,
    `## Optional`,
    ``,
    `- [${AUTHOR.name}](${AUTHOR.url}): ${t.footer.made}`,
    ``,
  ].join("\n");
}
