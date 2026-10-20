import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';
import { readdirSync } from 'node:fs';
import { wikiLinks } from './plugins/wiki-links.mjs';

export default defineConfig({
  site: process.env.SITE_ORIGIN ?? 'https://nora.67.zone',
  base: '/wiki',
  trailingSlash: 'always',
  redirects: {
    '/concepts/continuity/': '/wiki/concepts/sessionless-continuity/',
    '/architecture/ambient-cognition/': '/wiki/workflows/cognition/',
    '/architecture/memory/': '/wiki/concepts/memory-and-record/',
    '/workflows/computer/': '/wiki/systems/computer-and-browser/',
    '/operations/contributing/': '/wiki/contributing/changing-the-system/',
  },
  markdown: { processor: unified({ remarkPlugins: [wikiLinks] }) },
  integrations: [starlight({
    title: 'Nora · Wiki',
    components: { SiteTitle: './src/components/SiteTitle.astro' },
    favicon: new URL('/favicon.ico', process.env.SITE_ORIGIN ?? 'https://nora.67.zone').href,
    description: 'How Nora carries context, thinks between interactions and participates over time.',
    customCss: ['./src/styles/nora.css'],
    sidebar: [
      { label: 'Wiki home', link: '/' },
      { label: 'Start here', slug: 'quickstart' },
      ...[
        ['Concepts', 'concepts'],
        ['Architecture', 'architecture'],
        ['Workflows', 'workflows'],
        ['Systems', 'systems'],
        ['Integrations', 'integrations'],
        ['Operations', 'operations'],
        ['Testing', 'testing'],
        ['Contributing', 'contributing'],
      ].map(([label, directory]) => ({
        label,
        // Starlight's autogenerate assumes src/content/docs; our native wiki
        // lives outside that root. Discover slugs without copying its source.
        items: readdirSync(new URL(`../openwiki/${directory}/`, import.meta.url), { recursive: true })
          .filter(file => file.endsWith('.md') && !/(?:^|\/)index\.md$/.test(file))
          .sort()
          .map(file => ({ slug: `${directory}/${file.replace(/\.md$/, '')}` })),
      })),
      { label: 'Explore the graph', link: '/graph/' },
      { label: 'About Nora', link: new URL('/', process.env.SITE_ORIGIN ?? 'https://nora.67.zone').href },
    ],
  })],
});
