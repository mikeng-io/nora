import { getCollection } from 'astro:content';

export async function GET() {
  const pages = await getCollection('docs');
  const origin = (import.meta.env.SITE_ORIGIN ?? 'https://nora.67.zone').replace(/\/$/, '');
  const text = ['# Nora implementation wiki', '',
    'Source-grounded documentation of an experimental self-hosted social AI agent.', '',
    ...pages.sort((a, b) => a.id.localeCompare(b.id)).map((page) =>
      `- [${page.data.title}](${origin}/wiki/${page.id}/): ${page.data.description ?? ''}`),
    '',
  ].join('\n');
  return new Response(text, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
}
